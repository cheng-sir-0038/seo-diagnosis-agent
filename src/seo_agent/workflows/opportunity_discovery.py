from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from src.agent.observability import run_observed_tool
from src.agent.tools.base import ToolRuntimeContext
from src.core.cache import CacheManager
from src.core.config import settings
from src.core.exceptions import (
    InternalDataNotAvailableError,
    InternalProviderRequestError,
    InternalProviderResponseError,
)
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.chat.tools import FacadeProtocol, build_seo_agent_registry
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.opportunities.models import OpportunityCandidate, OpportunityDiscoveryOutput
from src.seo_agent.opportunities.service import OpportunityService, ScanRunService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.workflows.execution import evidence_rows, research_params
from src.seo_agent.workflows.keyword_opportunity import KeywordOpportunityArguments
from src.seo_agent.workflows.structured_model import StructuredModelRunner


class OpportunityDiscoveryRequest(KeywordOpportunityArguments):
    changed_by_actor_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=128)
    default_approver_actor_id: str | None = Field(default=None, min_length=1, max_length=200)
    max_opportunities: int = Field(default=3, ge=1, le=3)


class OpportunityDiscoveryWorkflow:
    """显式业务操作：研究、冻结证据、生成候选，再原子复用既有入池与审批服务。"""

    def __init__(
        self,
        database: SeoAgentDatabase,
        facade: FacadeProtocol,
        opportunities: OpportunityService,
        snapshots: CacheManager,
        model_runner: StructuredModelRunner,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.database, self.facade, self.opportunities = database, facade, opportunities
        self.snapshots, self.model_runner, self.observability = (
            snapshots,
            model_runner,
            observability,
        )
        # 同一进程先串行处理生成请求；数据库事务负责跨连接的写入去重。
        self._lock = asyncio.Lock()

    async def execute(
        self, scan_run_id: str, request: OpportunityDiscoveryRequest
    ) -> dict[str, Any]:
        query = request.query.strip()
        if not query or len(query.split()) > 10:
            raise ValueError("研究主题不能为空，且最多包含10个词")
        key = f"opportunity-discovery:{scan_run_id}:{request.idempotency_key}"
        request_hash = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        async with self._lock:
            existing = await self._receipt(self.database, key, request_hash)
            if existing is not None:
                async with observed_operation(
                    self.observability,
                    "seo.opportunity_discovery.reused",
                    kind=SpanKind.INTERNAL,
                    input_data={"scan_run_id": scan_run_id},
                    metadata={"reused": True, "source_trace_id": existing.get("trace_id")},
                ) as replay:
                    if replay is not None:
                        replay.output_data = existing
                return existing
            site = await self._context(self.database, scan_run_id, request)
            market = {
                "country": (request.country or site["default_country"]).upper(),
                "language": (request.language or site["default_language"])
                .lower()
                .replace("_", "-"),
            }
            registry = build_seo_agent_registry(self.facade, site, observability=self.observability)
            if "seo_keyword_opportunity" not in registry.names():
                raise InternalDataNotAvailableError("没有可用的关键词机会研究数据源")
            async with observed_operation(
                self.observability,
                "seo.opportunity_discovery",
                kind=SpanKind.INTERNAL,
                component_name="opportunity_discovery",
                input_data={"scan_run_id": scan_run_id, **request.model_dump()},
                trigger_type="api",
                metadata={"workflow_name": "opportunity_discovery", "site_id": site["id"]},
            ) as span:
                model = self.model_runner.model_factory()
                research_result = await run_observed_tool(
                    self.observability,
                    registry.get("seo_keyword_opportunity"),
                    {**research_params(request), "query": query},
                    runtime=ToolRuntimeContext(run_id=scan_run_id, site_id=site["id"]),
                    timeout=settings.agent_tool_timeout,
                )
                if not research_result.success:
                    raise InternalDataNotAvailableError("本次没有取得可用关键词证据")
                research = research_result.data
                evidence = []
                async with observed_operation(
                    self.observability,
                    "freeze_opportunity_evidence",
                    kind=SpanKind.INTERNAL,
                    input_data={"scan_run_id": scan_run_id},
                ) as freeze:
                    for step, payload in research["evidence"].items():
                        raw = payload.get("raw")
                        if not isinstance(raw, dict):
                            continue
                        frozen = {
                            **raw,
                            "meta": {
                                **raw.get("meta", {}),
                                "business_site_id": site["id"],
                                "scan_run_id": scan_run_id,
                            },
                        }
                        source = str(frozen["meta"].get("source") or "unknown")
                        snapshot_id = await self.snapshots.save_snapshot(
                            task_id=scan_run_id,
                            capability=frozen["meta"].get("capability", step),
                            data=frozen,
                            provider=source,
                        )
                        evidence.append({"id": snapshot_id, "step": step, "data": frozen})
                    if freeze is not None:
                        freeze.output_data = {
                            "evidence_snapshot_ids": [item["id"] for item in evidence]
                        }
                if not evidence:
                    raise InternalDataNotAvailableError("没有可冻结的研究证据")
                try:
                    invocation = await self.model_runner.invoke(
                        prompt_key="seo.opportunity.discover",
                        schema=OpportunityDiscoveryOutput,
                        context={
                            "site": {"id": site["id"], "domain": site["domain"]},
                            "query": query,
                            "market": market,
                            "research": research,
                            "evidence": evidence,
                            "max_opportunities": request.max_opportunities,
                        },
                        operation="opportunity_discovery",
                        business_object={"site_id": site["id"], "scan_run_id": scan_run_id},
                        model=model,
                    )
                except ValueError as exc:
                    raise InternalProviderResponseError("机会模型没有返回有效结构化输出") from exc
                except Exception as exc:
                    raise InternalProviderRequestError("机会候选模型调用暂不可用") from exc
                async with observed_operation(
                    self.observability,
                    "validate_opportunity_candidates",
                    kind=SpanKind.INTERNAL,
                    input_data={
                        "candidate_count": len(invocation.output.candidates),
                        "market": market,
                    },
                ) as validation:
                    candidates = self._validate(
                        invocation.output, research, evidence, market, request, site
                    )
                    if validation is not None:
                        validation.output_data = {
                            "candidates": [
                                candidate.model_dump(mode="json") for candidate in candidates
                            ]
                        }
                async with observed_operation(
                    self.observability,
                    "persist_opportunity_candidates",
                    kind=SpanKind.INTERNAL,
                    input_data={"scan_run_id": scan_run_id, "candidate_count": len(candidates)},
                ) as write:
                    async with self.database.transaction() as transaction:
                        existing = await self._receipt(transaction, key, request_hash)
                        if existing is not None:
                            if write is not None:
                                write.output_data = {
                                    "reused": True,
                                    "opportunity_ids": existing["opportunity_ids"],
                                }
                            if span is not None:
                                span.output_data = existing
                            return existing
                        await self._context(transaction, scan_run_id, request)
                        service = self._service(transaction)
                        ids = []
                        states = []
                        for candidate in candidates:
                            fingerprint = service.fingerprint(site["id"], candidate)
                            opportunity = await service.upsert_candidate(
                                site_id=site["id"],
                                scan_run_id=scan_run_id,
                                candidate=candidate,
                                changed_by_actor_id=request.changed_by_actor_id,
                                default_approver_actor_id=request.default_approver_actor_id,
                                idempotency_key=f"{key}:{fingerprint}",
                            )
                            ids.append(opportunity["id"])
                            states.append(
                                {
                                    field: opportunity[field]
                                    for field in ("id", "status", "priority_level", "current_score")
                                }
                            )
                        result = {
                            "site_id": site["id"],
                            "scan_run_id": scan_run_id,
                            "workflow_status": research["workflow_status"],
                            "opportunity_ids": ids,
                            "evidence_snapshot_ids": [item["id"] for item in evidence],
                            "trace_id": span.trace_id if span else invocation.trace_id,
                            "limitations": research["limitations"]
                            + ["入池不等于批准，不会创建任务或发布内容。"],
                        }
                        await ScanRunService(transaction).complete(scan_run_id)
                        await SeoAgentSystemService(transaction).append_work_record(
                            site_id=site["id"],
                            title="完成机会研究与入池",
                            summary=f"本次生成{len(ids)}个待决策机会，审批规则沿用现有策略。",
                            record_type="opportunity_discovery_completed",
                            actor_id=request.changed_by_actor_id,
                            idempotency_key=key,
                            payload={"request_hash": request_hash, "result": result},
                        )
                    if write is not None:
                        write.output_data = {"opportunity_ids": ids, "opportunities": states}
                if span is not None:
                    span.output_data = result
                return result

    async def _context(
        self,
        database: SeoAgentDatabase,
        scan_run_id: str,
        request: OpportunityDiscoveryRequest,
    ) -> dict[str, Any]:
        scan = await database.fetch_one(
            "SELECT s.status, w.site_id, w.schedule_type, w.archived_at FROM schedule_runs s "
            "JOIN work_schedules w ON w.id = s.schedule_id WHERE s.id = ?",
            (scan_run_id,),
        )
        if scan is None:
            raise KeyError("扫描运行不存在")
        if (
            scan["status"] != "RUNNING"
            or scan["archived_at"] is not None
            or scan["schedule_type"] != "OPPORTUNITY_SCAN"
        ):
            raise ValueError("扫描运行当前不可执行机会发现")
        site = await SeoAgentSystemService(database).get_site(scan["site_id"])
        if site["status"] != "ACTIVE" or site.get("archived_at") is not None:
            raise ValueError("站点当前不可用于机会发现")
        for actor_id in (request.changed_by_actor_id, request.default_approver_actor_id):
            if actor_id is None:
                continue
            actor = await database.fetch_one(
                "SELECT status, archived_at FROM actors WHERE id = ?", (actor_id,)
            )
            if actor is None:
                raise KeyError("操作人或审批人不存在")
            if actor["status"] != "ACTIVE" or actor["archived_at"] is not None:
                raise ValueError("操作人或审批人当前不可用")
        return site

    @staticmethod
    async def _receipt(
        database: SeoAgentDatabase,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = await database.fetch_one(
            "SELECT payload_json FROM work_records WHERE idempotency_key = ?", (key,)
        )
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if payload.get("request_hash") != request_hash:
            raise ValueError("幂等键已被不同的请求内容使用")
        return payload["result"]

    def _service(self, database: SeoAgentDatabase) -> OpportunityService:
        original = self.opportunities
        return OpportunityService(
            database,
            policy=original.policy,
            normalizer=original.normalizer,
            action_policy=original.action_policy,
            approvals=ApprovalService(database, reminder_hours=original.approvals.reminder_hours)
            if original.approvals
            else None,
            notifications=NotificationService(database, original.notifications.provider)
            if original.notifications
            else None,
        )

    def _validate(
        self,
        output: OpportunityDiscoveryOutput,
        research: dict[str, Any],
        evidence: list[dict[str, Any]],
        market: dict[str, str],
        request: OpportunityDiscoveryRequest,
        site: dict[str, Any],
    ) -> list[OpportunityCandidate]:
        if len(output.candidates) > request.max_opportunities:
            raise ValueError("模型返回的候选数量超过本次上限")
        normalizer = self.opportunities.normalizer
        known = {
            normalizer.normalize_text(row["keyword"]): row for row in research["keyword_candidates"]
        }
        observed = {
            normalizer.normalize_text(row["value"])
            for row in evidence_rows(research["evidence"].get("suggestions"))
            if isinstance(row.get("value"), str)
        }
        observed.update(
            normalizer.normalize_text(row["keyword"])
            for row in evidence_rows(research["evidence"].get("metrics"))
            if isinstance(row.get("keyword"), str)
        )
        serp = evidence_rows(research["evidence"].get("serp"))
        if serp:
            observed.add(normalizer.normalize_text(request.query))
        site_domain = urlsplit(
            site["domain"] if "://" in site["domain"] else "https://" + site["domain"]
        ).hostname
        pages = set()
        unresolved_pages = set()
        for row in serp:
            if not isinstance(row.get("url"), str):
                continue
            try:
                parsed = urlsplit(row["url"])
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    continue
                if parsed.hostname.removeprefix("www.") != (site_domain or "").removeprefix("www."):
                    page = normalizer.normalize_url(row["url"])
                    pages.add(page)
                    # Google 的不透明跳转地址不能证明实际竞品域名。
                    if parsed.path == "/goto" and (
                        parsed.hostname == "google.com" or parsed.hostname.endswith(".google.com")
                    ):
                        unresolved_pages.add(page)
            except ValueError:
                continue
        allowed_ids = {item["id"] for item in evidence}
        supported_keywords = {}
        metric_ids = set()
        serp_ids = set()
        for item in evidence:
            rows = item["data"].get("data", [])
            words = set()
            if item["step"] in {"suggestions", "metrics"}:
                field = "value" if item["step"] == "suggestions" else "keyword"
                words = {
                    normalizer.normalize_text(row[field])
                    for row in rows
                    if isinstance(row, dict) and isinstance(row.get(field), str)
                }
                if item["step"] == "metrics":
                    metric_ids.add(item["id"])
            elif item["step"] == "serp" and rows:
                words = {normalizer.normalize_text(request.query)}
                serp_ids.add(item["id"])
            supported_keywords[item["id"]] = words
        candidates, fingerprints = [], set()
        for raw in output.candidates:
            candidate = normalizer.normalize(raw)
            if (
                not candidate.normalized_intent
                or not candidate.opportunity_type
                or not candidate.title
                or not candidate.summary
            ):
                raise ValueError("候选业务描述不能为空")
            if (
                candidate.target_country != market["country"]
                or candidate.target_language != market["language"]
            ):
                raise ValueError("候选市场与本次研究范围不一致")
            if not candidate.keywords or not set(candidate.keywords) <= (set(known) & observed):
                raise ValueError("候选关键词不在本次已观察的证据中")
            if (
                not candidate.evidence_snapshot_ids
                or not set(candidate.evidence_snapshot_ids) <= allowed_ids
            ):
                raise ValueError("候选引用了不属于本次研究的证据")
            cited = set(candidate.evidence_snapshot_ids)
            supported = set().union(*(supported_keywords[evidence_id] for evidence_id in cited))
            if not set(candidate.keywords) <= supported:
                raise ValueError("候选引用的证据未包含所列关键词")
            if not set(candidate.competitor_pages) <= pages:
                raise ValueError("候选竞品页面不在本次搜索证据中")
            if candidate.competitor_pages and not cited & serp_ids:
                raise ValueError("候选竞品页面缺少搜索证据引用")
            primary = known[candidate.keywords[0]]
            if (
                any(
                    primary.get(field) is not None
                    for field in ("search_volume", "keyword_difficulty")
                )
                and not cited & metric_ids
            ):
                raise ValueError("候选的关键词指标缺少来源引用")
            candidate = candidate.model_copy(
                update={
                    "search_volume": primary.get("search_volume"),
                    "keyword_difficulty": primary.get("keyword_difficulty"),
                    "competitor_pages": [
                        page for page in candidate.competitor_pages if page not in unresolved_pages
                    ],
                    "competitor_count": len({urlsplit(page).hostname for page in pages})
                    if (
                        candidate.keywords[0] == normalizer.normalize_text(request.query)
                        and serp
                        and not unresolved_pages
                    )
                    else None,
                    "summary": candidate.summary + (
                        " SERP包含未解析的搜索引擎跳转地址，未确认真实竞品；"
                        "跳转地址不作为竞品入库，竞品数量保持未知。"
                        if unresolved_pages else ""
                    ),
                }
            )
            fingerprint = normalizer.fingerprint(site["id"], candidate)
            if fingerprint not in fingerprints:
                candidates.append(candidate)
                fingerprints.add(fingerprint)
        return candidates
