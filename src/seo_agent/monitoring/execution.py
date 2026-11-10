from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from src.observability.client import ObservabilityClient
from src.observability.models import SpanKind
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.monitoring.service import MonitoringService


class IndexingObservation(BaseModel):
    indexed: bool
    evidence_snapshot_id: str | None = None
    observed_at: str | None = None


class RankingObservation(BaseModel):
    position: float = Field(gt=0)
    evidence_snapshot_id: str | None = None
    observed_at: str | None = None


class IndexingCollector(Protocol):
    async def collect(self, *, published_url: str) -> IndexingObservation: ...


class RankingCollector(Protocol):
    async def collect(
        self,
        *,
        published_url: str,
        keyword: str,
    ) -> RankingObservation: ...


class MonitoringExecutionResult(BaseModel):
    trace_id: str
    monitoring_plan_id: str
    check_id: str
    check_type: str
    status: str


class MonitoringExecutionService:
    """把长期监控拆成独立、可追溯的短 Trace。"""

    def __init__(
        self,
        database: SeoAgentDatabase,
        monitoring: MonitoringService,
        observability: ObservabilityClient,
    ) -> None:
        self.database = database
        self.monitoring = monitoring
        self.observability = observability

    async def run_indexing_check(
        self,
        *,
        check_id: str,
        collector: IndexingCollector,
    ) -> MonitoringExecutionResult:
        context = await self._get_context(check_id, expected_type="INDEXING")
        async with self.observability.trace(
            "seo.monitoring.indexing",
            input_data={
                "check_id": check_id,
                "monitoring_plan_id": context["monitoring_plan_id"],
                "publish_record_id": context["publish_record_id"],
            },
        ) as trace:
            await self._start_check(check_id, trace.trace_id)
            await self._link_origin(context, trace.trace_id, check_id)
            try:
                async with trace.run(
                    trigger_type="scheduled",
                    workflow_name="monitoring_indexing",
                    metadata={"check_id": check_id},
                ) as run:
                    async with run.span(
                        "collect_indexing",
                        kind=SpanKind.TOOL,
                        component_name=collector.__class__.__name__,
                    ) as span:
                        started = time.perf_counter()
                        observation = await collector.collect(
                            published_url=context["published_url"]
                        )
                        await span.tool_call(
                            tool_call_id=f"monitor-index-{uuid.uuid4().hex}",
                            tool_name="monitoring.indexing.collect",
                            adapter_key=collector.__class__.__name__,
                            operation="collect",
                            arguments={"published_url": context["published_url"]},
                            result_summary={"indexed": observation.indexed},
                            latency_ms=round((time.perf_counter() - started) * 1000, 3),
                        )
                    await self.monitoring.record_indexing_result(
                        check_id,
                        indexed=observation.indexed,
                        evidence_snapshot_id=observation.evidence_snapshot_id,
                        observed_at=observation.observed_at,
                    )
            except Exception as exc:
                await self._fail_check(check_id, exc)
                raise
        return MonitoringExecutionResult(
            trace_id=trace.trace_id,
            monitoring_plan_id=context["monitoring_plan_id"],
            check_id=check_id,
            check_type="INDEXING",
            status="COMPLETED",
        )

    async def ingest_indexing_result(
        self,
        *,
        check_id: str,
        observation: IndexingObservation,
    ) -> dict:
        context = await self._get_context(check_id, expected_type="INDEXING")
        async with self.observability.trace(
            "seo.monitoring.indexing.ingest",
            input_data={
                "check_id": check_id,
                "monitoring_plan_id": context["monitoring_plan_id"],
                "indexed": observation.indexed,
            },
        ) as trace:
            await self._start_check(check_id, trace.trace_id)
            await self._link_origin(context, trace.trace_id, check_id)
            try:
                async with trace.run(
                    trigger_type="api",
                    workflow_name="monitoring_indexing_ingestion",
                    metadata={"check_id": check_id},
                ) as run:
                    async with run.span(
                        "ingest_indexing_result",
                        kind=SpanKind.CUSTOM,
                        component_name="monitoring_api",
                    ) as span:
                        await span.event(
                            "monitoring.result.ingested",
                            {"indexed": observation.indexed},
                        )
                        result = await self.monitoring.record_indexing_result(
                            check_id,
                            indexed=observation.indexed,
                            evidence_snapshot_id=observation.evidence_snapshot_id,
                            observed_at=observation.observed_at,
                        )
            except Exception as exc:
                await self._fail_check(check_id, exc)
                raise
        result["execution_trace_id"] = trace.trace_id
        return result

    async def ingest_ranking_result(
        self,
        *,
        check_id: str,
        keyword: str,
        observation: RankingObservation,
    ) -> dict:
        context = await self._get_context(check_id, expected_type="RANKING")
        async with self.observability.trace(
            "seo.monitoring.ranking.ingest",
            input_data={
                "check_id": check_id,
                "monitoring_plan_id": context["monitoring_plan_id"],
                "keyword": keyword,
                "position": observation.position,
            },
        ) as trace:
            await self._start_check(check_id, trace.trace_id)
            await self._link_origin(context, trace.trace_id, check_id)
            try:
                async with trace.run(
                    trigger_type="api",
                    workflow_name="monitoring_ranking_ingestion",
                    metadata={"check_id": check_id, "keyword": keyword},
                ) as run:
                    async with run.span(
                        "ingest_ranking_result",
                        kind=SpanKind.CUSTOM,
                        component_name="monitoring_api",
                    ) as span:
                        await span.event(
                            "monitoring.result.ingested",
                            {"keyword": keyword, "position": observation.position},
                        )
                        result = await self.monitoring.record_ranking_observation(
                            check_id,
                            keyword=keyword,
                            position=observation.position,
                            evidence_snapshot_id=observation.evidence_snapshot_id,
                            observed_at=observation.observed_at,
                        )
            except Exception as exc:
                await self._fail_check(check_id, exc)
                raise
        result["execution_trace_id"] = trace.trace_id
        return result

    async def run_ranking_check(
        self,
        *,
        check_id: str,
        keyword: str,
        collector: RankingCollector,
    ) -> MonitoringExecutionResult:
        context = await self._get_context(check_id, expected_type="RANKING")
        async with self.observability.trace(
            "seo.monitoring.ranking",
            input_data={
                "check_id": check_id,
                "monitoring_plan_id": context["monitoring_plan_id"],
                "keyword": keyword,
            },
        ) as trace:
            await self._start_check(check_id, trace.trace_id)
            await self._link_origin(context, trace.trace_id, check_id)
            try:
                async with trace.run(
                    trigger_type="scheduled",
                    workflow_name="monitoring_ranking",
                    metadata={"check_id": check_id, "keyword": keyword},
                ) as run:
                    async with run.span(
                        "collect_ranking",
                        kind=SpanKind.TOOL,
                        component_name=collector.__class__.__name__,
                    ) as span:
                        started = time.perf_counter()
                        observation = await collector.collect(
                            published_url=context["published_url"],
                            keyword=keyword,
                        )
                        await span.tool_call(
                            tool_call_id=f"monitor-rank-{uuid.uuid4().hex}",
                            tool_name="monitoring.ranking.collect",
                            adapter_key=collector.__class__.__name__,
                            operation="collect",
                            arguments={
                                "published_url": context["published_url"],
                                "keyword": keyword,
                            },
                            result_summary={"position": observation.position},
                            latency_ms=round((time.perf_counter() - started) * 1000, 3),
                        )
                    await self.monitoring.record_ranking_observation(
                        check_id,
                        keyword=keyword,
                        position=observation.position,
                        evidence_snapshot_id=observation.evidence_snapshot_id,
                        observed_at=observation.observed_at,
                    )
            except Exception as exc:
                await self._fail_check(check_id, exc)
                raise
        return MonitoringExecutionResult(
            trace_id=trace.trace_id,
            monitoring_plan_id=context["monitoring_plan_id"],
            check_id=check_id,
            check_type="RANKING",
            status="COMPLETED",
        )

    async def _get_context(self, check_id: str, *, expected_type: str) -> dict:
        row = await self.database.fetch_one(
            "SELECT c.*, p.publish_record_id, p.origin_trace_id, r.published_url "
            "FROM monitoring_checks c "
            "JOIN monitoring_plans p ON p.id = c.monitoring_plan_id "
            "JOIN publish_records r ON r.id = p.publish_record_id "
            "WHERE c.id = ?",
            (check_id,),
        )
        if row is None:
            raise KeyError(f"监控检查不存在: {check_id}")
        if row["check_type"] != expected_type:
            raise ValueError(f"监控检查类型不匹配: {row['check_type']}")
        if not row.get("published_url"):
            raise ValueError("发布 URL 尚未确认")
        return row

    async def _start_check(self, check_id: str, trace_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self.database.execute(
            "UPDATE monitoring_checks SET status = 'RUNNING', "
            "started_at = COALESCE(started_at, ?), trace_id = ? WHERE id = ?",
            (now, trace_id, check_id),
        )

    async def _link_origin(self, context: dict, trace_id: str, check_id: str) -> None:
        origin_trace_id = context.get("origin_trace_id")
        if not origin_trace_id:
            return
        await self.observability.link_traces(
            source_trace_id=origin_trace_id,
            target_trace_id=trace_id,
            relation_type="FOLLOW_UP",
            attributes={
                "monitoring_plan_id": context["monitoring_plan_id"],
                "monitoring_check_id": check_id,
                "check_type": context["check_type"],
            },
        )

    async def _fail_check(self, check_id: str, exc: Exception) -> None:
        await self.database.execute(
            "UPDATE monitoring_checks SET status = 'FAILED', completed_at = ?, "
            "error_code = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), type(exc).__name__, check_id),
        )
