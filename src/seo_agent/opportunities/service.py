from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.seo_agent.approvals.policy import ActionPolicy
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    ApprovalActionRow,
    CompetitorPageRow,
    ExecutionTaskRow,
    KeywordRow,
    OpportunityCompetitorPageRow,
    OpportunityKeywordRow,
    OpportunityObservationEvidenceRow,
    OpportunityObservationRow,
    OpportunityRow,
    OpportunityStatusHistoryRow,
    ScheduleRunRow,
    SiteRow,
    TaskOpportunityRow,
    WorkRecordRow,
    WorkScheduleRow,
)
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.opportunities.models import OpportunityCandidate
from src.seo_agent.opportunities.normalizer import OpportunityCandidateNormalizer
from src.seo_agent.opportunities.policy import OpportunityPolicy
from src.seo_agent.services.system import SeoAgentSystemService


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class OpportunityRequestConflictError(ValueError):
    """An idempotency key already belongs to another scan or ingestion request."""


class ScanRunService:
    def __init__(self, database: SeoAgentDatabase) -> None:
        self.database = database

    async def start(
        self,
        *,
        schedule_id: str,
        trigger_type: str,
        idempotency_key: str,
        scheduled_at: str | None = None,
    ) -> dict[str, Any]:
        trigger = trigger_type.upper()
        async with self.database.transaction() as tx:
            # Serialize starts for this schedule, including runs skipped during an overlap.
            schedule = await tx.fetch_value(
                select(WorkScheduleRow.id)
                .where(WorkScheduleRow.id == schedule_id)
                .with_for_update()
            )
            if schedule is None:
                raise KeyError(f"计划不存在: {schedule_id}")
            by_key = select(ScheduleRunRow.__table__).where(
                ScheduleRunRow.idempotency_key == idempotency_key
            )
            result = await tx.fetch_one(by_key)
            if result is None:
                running = await tx.fetch_one(
                    select(ScheduleRunRow.__table__).where(
                        ScheduleRunRow.schedule_id == schedule_id,
                        ScheduleRunRow.status == "RUNNING",
                    )
                )
                if running is not None and trigger == "MANUAL":
                    return running
                now = _now()
                values = {
                    "id": _id("scan"), "schedule_id": schedule_id,
                    "trigger_type": trigger, "scheduled_at": scheduled_at,
                    "idempotency_key": idempotency_key, "created_at": now,
                }
                if running is None:
                    values.update(status="RUNNING", started_at=now)
                else:
                    values.update(
                        status="SKIPPED_ALREADY_RUNNING", skip_reason="already_running",
                        existing_run_id=running["id"], completed_at=now,
                    )
                await tx.execute(
                    pg_insert(ScheduleRunRow).values(**values)
                    .on_conflict_do_nothing(index_elements=[ScheduleRunRow.idempotency_key])
                )
                result = await tx.fetch_one(by_key)
                assert result is not None
            if (result["schedule_id"], result["trigger_type"], result["scheduled_at"]) != (
                schedule_id, trigger, scheduled_at,
            ):
                raise OpportunityRequestConflictError("幂等键已被其他扫描请求使用")
            return result

    async def get(self, run_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            select(ScheduleRunRow.__table__).where(ScheduleRunRow.id == run_id)
        )
        if row is None:
            raise KeyError(f"扫描运行不存在: {run_id}")
        return row

    async def complete(self, run_id: str, *, status: str = "COMPLETED") -> dict[str, Any]:
        async with self.database.transaction() as tx:
            now = _now()
            run = await tx.fetch_one(
                update(ScheduleRunRow).where(ScheduleRunRow.id == run_id)
                .values(status=status, completed_at=now)
                .returning(*ScheduleRunRow.__table__.columns)
            )
            if run is None:
                raise KeyError(f"扫描运行不存在: {run_id}")
            await tx.execute(
                update(WorkScheduleRow).where(WorkScheduleRow.id == run["schedule_id"])
                .values(last_run_at=now, updated_at=now)
            )
            return run


class OpportunityService:
    def __init__(
        self,
        database: SeoAgentDatabase,
        *,
        approvals: ApprovalService | None = None,
        notifications: NotificationService | None = None,
        policy: OpportunityPolicy | None = None,
        normalizer: OpportunityCandidateNormalizer | None = None,
        action_policy: ActionPolicy | None = None,
    ) -> None:
        self.database = database
        self.approvals = approvals
        self.notifications = notifications
        self.policy = policy or OpportunityPolicy()
        self.normalizer = normalizer or OpportunityCandidateNormalizer()
        self.action_policy = action_policy or ActionPolicy()
        self.system = SeoAgentSystemService(database)

    def fingerprint(self, site_id: str, candidate: OpportunityCandidate) -> str:
        return self.normalizer.fingerprint(site_id, candidate)

    def using(self, database: SeoAgentDatabase) -> OpportunityService:
        if database is self.database:
            return self
        return OpportunityService(
            database,
            approvals=ApprovalService(database, reminder_hours=self.approvals.reminder_hours)
            if self.approvals else None,
            notifications=NotificationService(database, self.notifications.provider)
            if self.notifications else None,
            policy=self.policy, normalizer=self.normalizer, action_policy=self.action_policy,
        )

    async def upsert_candidate(
        self,
        *,
        site_id: str,
        scan_run_id: str,
        candidate: OpportunityCandidate,
        changed_by_actor_id: str,
        idempotency_key: str,
        default_approver_actor_id: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.transaction() as tx:
            # Lock the site before comparing observations or creating a new fingerprint.
            if await tx.fetch_value(
                select(SiteRow.id).where(SiteRow.id == site_id).with_for_update(key_share=True)
            ) is None:
                raise KeyError(f"站点不存在: {site_id}")
            # Work record keys are globally unique, including across different sites.
            await tx.fetch_value(select(func.pg_advisory_xact_lock(
                func.hashtextextended(f"candidate:{idempotency_key}", 0)
            )))
            return await self.using(tx)._ingest(
                site_id=site_id, scan_run_id=scan_run_id, candidate=candidate,
                changed_by_actor_id=changed_by_actor_id, idempotency_key=idempotency_key,
                default_approver_actor_id=default_approver_actor_id,
            )

    async def _ingest(
        self,
        *,
        site_id: str,
        scan_run_id: str,
        candidate: OpportunityCandidate,
        changed_by_actor_id: str,
        idempotency_key: str,
        default_approver_actor_id: str | None,
    ) -> dict[str, Any]:
        candidate = self.normalizer.normalize(candidate)
        fingerprint = self.fingerprint(site_id, candidate)
        journal = await self.database.fetch_one(
            select(WorkRecordRow.site_id, WorkRecordRow.payload_json).where(
                WorkRecordRow.idempotency_key == idempotency_key
            )
        )
        if journal is not None:
            payload = json.loads(journal["payload_json"])
            if (journal["site_id"] != site_id or payload.get("scan_run_id") != scan_run_id
                    or not payload.get("opportunity_id")):
                raise OpportunityRequestConflictError("幂等键已被其他入池请求使用")
            opportunity = await self.get(payload["opportunity_id"])
            if opportunity["fingerprint"] != fingerprint:
                raise OpportunityRequestConflictError("幂等键已被其他入池请求使用")
            return opportunity
        existing = await self.database.fetch_one(
            select(OpportunityRow.__table__).where(
                OpportunityRow.site_id == site_id, OpportunityRow.fingerprint == fingerprint
            ).with_for_update()
        )
        previous_observation = None
        if existing is not None and existing["status"] == "REJECTED":
            previous_observation = await self.database.fetch_one(
                select(OpportunityObservationRow.__table__)
                .where(OpportunityObservationRow.opportunity_id == existing["id"])
                .order_by(OpportunityObservationRow.observed_at.desc()).limit(1)
            )
        score = self.policy.score(candidate)
        priority_status = {
            "HIGH": "WAITING_FOR_DECISION",
            "MEDIUM": "OPPORTUNITY_POOL",
            "LOW": "OBSERVED",
        }[score.priority]
        now = _now()
        triggered: list[str] = []
        reopened = False
        if existing is None:
            opportunity_id = _id("opportunity")
            await self.database.execute(
                insert(OpportunityRow).values(
                    id=opportunity_id, site_id=site_id, fingerprint=fingerprint,
                    normalized_intent=candidate.normalized_intent,
                    opportunity_type=candidate.opportunity_type,
                    target_country=candidate.target_country,
                    target_language=candidate.target_language,
                    title=candidate.title, current_score=score.total, priority_level=score.priority,
                    status=priority_status, current_summary=candidate.summary,
                    policy_version=self.policy.version, first_seen_at=now, last_seen_at=now,
                    seen_count=1, created_at=now, updated_at=now,
                )
            )
            await self._status_history(
                opportunity_id,
                None,
                priority_status,
                "首次发现并完成评分",
                changed_by_actor_id,
                triggered,
            )
        else:
            opportunity_id = existing["id"]
            target_status = existing["status"]
            if existing["status"] == "REJECTED":
                major, triggered = self.policy.is_major_change(
                    previous_score=(
                        float(previous_observation["score"])
                        if previous_observation is not None
                        else float(existing["current_score"] or 0)
                    ),
                    current_score=float(score.total),
                    previous_search_volume=(
                        previous_observation["search_volume"]
                        if previous_observation is not None
                        else None
                    ),
                    current_search_volume=candidate.search_volume,
                    explicit_reasons=candidate.major_change_reasons,
                )
                if major and score.priority == "HIGH":
                    target_status = "WAITING_FOR_DECISION"
                    reopened = True
            elif existing["status"] in {"OBSERVED", "OPPORTUNITY_POOL", "WAITING_FOR_DECISION"}:
                target_status = priority_status
            await self.database.execute(
                update(OpportunityRow).where(OpportunityRow.id == opportunity_id).values(
                    title=candidate.title, current_score=score.total, priority_level=score.priority,
                    status=target_status, current_summary=candidate.summary,
                    policy_version=self.policy.version, last_seen_at=now,
                    seen_count=OpportunityRow.seen_count + 1, updated_at=now,
                    **({"reopened_at": now} if reopened else {}),
                )
            )
            if target_status != existing["status"]:
                if reopened:
                    await self._status_history(
                        opportunity_id,
                        existing["status"],
                        "REOPENED",
                        "重大证据变化触发重新打开",
                        changed_by_actor_id,
                        triggered,
                    )
                    await self._status_history(
                        opportunity_id,
                        "REOPENED",
                        target_status,
                        "重新进入具体动作决策",
                        changed_by_actor_id,
                        triggered,
                    )
                else:
                    await self._status_history(
                        opportunity_id,
                        existing["status"],
                        target_status,
                        "新观测导致分层变化",
                        changed_by_actor_id,
                        triggered,
                    )
        observation_id = _id("observation")
        await self.database.execute(
            insert(OpportunityObservationRow).values(
                id=observation_id, opportunity_id=opportunity_id, score=score.total,
                priority_level=score.priority, search_volume=candidate.search_volume,
                keyword_difficulty=candidate.keyword_difficulty,
                competitor_count=candidate.competitor_count, gap_summary=candidate.summary,
                score_factors_json=score.factors.model_dump_json(),
                policy_version=self.policy.version,
                threshold_snapshot_json=json.dumps({
                    "high": self.policy.high_threshold,
                    "medium": self.policy.medium_threshold,
                    "reopen_score": self.policy.reopen_score_increase,
                    "reopen_volume_percent": self.policy.reopen_search_volume_growth_percent,
                }),
                observed_at=now, created_at=now,
            )
        )
        for evidence_id in candidate.evidence_snapshot_ids:
            await self.database.execute(
                pg_insert(OpportunityObservationEvidenceRow).values(
                    id=_id("oppevidence"), observation_id=observation_id,
                    evidence_snapshot_id=evidence_id, evidence_role="SCORE_INPUT",
                    contribution_weight=1.0, created_at=now,
                ).on_conflict_do_nothing(index_elements=[
                    OpportunityObservationEvidenceRow.observation_id,
                    OpportunityObservationEvidenceRow.evidence_snapshot_id,
                    OpportunityObservationEvidenceRow.evidence_role,
                ])
            )
        await self._link_keywords(opportunity_id, candidate, now)
        await self._link_competitor_pages(site_id, opportunity_id, candidate, now)
        await self.system.append_work_record(
            site_id=site_id,
            title=("重新打开机会" if reopened else "更新搜索机会" if existing else "发现搜索机会"),
            summary=f"{candidate.title}，评分 {score.total}，优先级 {score.priority}",
            record_type=(
                "opportunity_reopened"
                if reopened
                else "opportunity_updated"
                if existing
                else "opportunity_discovered"
            ),
            actor_id=changed_by_actor_id,
            idempotency_key=idempotency_key,
            payload={
                "opportunity_id": opportunity_id,
                "scan_run_id": scan_run_id,
                "score": score.total,
                "priority": score.priority,
                "triggered_conditions": triggered,
            },
        )
        current = await self.get(opportunity_id)
        if current["status"] == "WAITING_FOR_DECISION" and default_approver_actor_id:
            await self._ensure_decision_request(
                current,
                observation_id=observation_id,
                approver_actor_id=default_approver_actor_id,
                changed_by_actor_id=changed_by_actor_id,
            )
        return await self.get(opportunity_id)

    async def _link_keywords(
        self,
        opportunity_id: str,
        candidate: OpportunityCandidate,
        now: str,
    ) -> None:
        # Acquire global keyword rows in a consistent order when different sites share keywords.
        for index, keyword in sorted(enumerate(candidate.keywords), key=lambda item: item[1]):
            scope = {
                "normalized_keyword": keyword,
                "language": candidate.target_language,
                "country": candidate.target_country,
            }
            await self.database.execute(
                pg_insert(KeywordRow)
                .values(id=_id("keyword"), **scope, created_at=now, updated_at=now)
                .on_conflict_do_nothing(index_elements=[
                    KeywordRow.normalized_keyword, KeywordRow.language, KeywordRow.country,
                ])
            )
            keyword_id = await self.database.fetch_value(select(KeywordRow.id).filter_by(**scope))
            await self.database.execute(
                pg_insert(OpportunityKeywordRow).values(
                    id=_id("oppkeyword"), opportunity_id=opportunity_id, keyword_id=keyword_id,
                    keyword_role="PRIMARY" if index == 0 else "SECONDARY",
                    relevance_score=1.0 if index == 0 else 0.8, first_seen_at=now,
                    last_seen_at=now, created_at=now,
                ).on_conflict_do_nothing(index_elements=[
                    OpportunityKeywordRow.opportunity_id, OpportunityKeywordRow.keyword_id,
                    OpportunityKeywordRow.keyword_role,
                ])
            )
            await self.database.execute(
                update(OpportunityKeywordRow).where(
                    OpportunityKeywordRow.opportunity_id == opportunity_id,
                    OpportunityKeywordRow.keyword_id == keyword_id,
                ).values(last_seen_at=now)
            )

    async def _link_competitor_pages(
        self,
        site_id: str,
        opportunity_id: str,
        candidate: OpportunityCandidate,
        now: str,
    ) -> None:
        evidence_snapshot_id = (
            candidate.evidence_snapshot_ids[0] if candidate.evidence_snapshot_ids else None
        )
        for url in candidate.competitor_pages:
            domain = (urlsplit(url).hostname or "unknown").lower()
            page_id = await self.database.fetch_value(
                pg_insert(CompetitorPageRow).values(
                    id=_id("competitor_page"), site_id=site_id, competitor_domain=domain, url=url,
                    first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
                ).on_conflict_do_update(
                    index_elements=[CompetitorPageRow.site_id, CompetitorPageRow.url],
                    set_={"last_seen_at": now, "updated_at": now},
                ).returning(CompetitorPageRow.id)
            )
            await self.database.execute(
                pg_insert(OpportunityCompetitorPageRow).values(
                    id=_id("opp_competitor"), opportunity_id=opportunity_id,
                    competitor_page_id=page_id, gap_type=candidate.opportunity_type,
                    relevance_score=1.0, evidence_snapshot_id=evidence_snapshot_id, created_at=now,
                ).on_conflict_do_nothing(index_elements=[
                    OpportunityCompetitorPageRow.opportunity_id,
                    OpportunityCompetitorPageRow.competitor_page_id,
                    OpportunityCompetitorPageRow.gap_type,
                ])
            )

    async def _ensure_decision_request(
        self,
        opportunity: dict[str, Any],
        *,
        observation_id: str,
        approver_actor_id: str,
        changed_by_actor_id: str,
    ) -> None:
        if self.approvals is None:
            return
        active = await self.database.fetch_value(
            select(ApprovalActionRow.id).where(
                ApprovalActionRow.target_type == "opportunity",
                ApprovalActionRow.target_id == opportunity["id"],
                ApprovalActionRow.status == "WAITING_FOR_APPROVAL",
            ).order_by(ApprovalActionRow.requested_at.desc()).limit(1)
        )
        if active is not None:
            return
        policy = self.action_policy.evaluate(
            action_type="ADD_OPPORTUNITY_TO_PRODUCTION",
            target_environment="production",
        )
        if not policy.requires_approval:
            return
        action = await self.approvals.create_action(
            site_id=opportunity["site_id"],
            action_type=policy.action_type,
            risk_level=policy.risk_level,
            target_type="opportunity",
            target_id=opportunity["id"],
            requested_payload={
                "score": opportunity["current_score"],
                "priority": opportunity["priority_level"],
                "recommended_task_types": ["ARTICLE"],
                "action_policy_version": policy.policy_version,
                "action_policy_reason": policy.reason,
            },
            approver_actor_id=approver_actor_id,
            idempotency_key=f"opportunity-approval:{opportunity['id']}:{observation_id}",
            requested_by_actor_id=changed_by_actor_id,
        )
        if self.notifications is not None:
            await self.notifications.create_event(
                site_id=opportunity["site_id"],
                event_key="approval_requested",
                business_object_type="opportunity",
                business_object_id=opportunity["id"],
                title=f"高价值机会等待决策：{opportunity['title']}",
                content={
                    "summary": opportunity["current_summary"],
                    "approval_action_id": action["id"],
                    "score": opportunity["current_score"],
                },
                priority="HIGH",
                idempotency_key=f"opportunity-approval-email:{action['id']}",
            )

    async def reject_opportunity(
        self, opportunity_id: str, *, reason: str, actor_id: str
    ) -> dict[str, Any]:
        async with self.database.transaction() as tx:
            service = self.using(tx)
            previous = await tx.fetch_value(
                select(OpportunityRow.status).where(OpportunityRow.id == opportunity_id)
                .with_for_update()
            )
            if previous is None:
                raise KeyError(f"机会不存在: {opportunity_id}")
            if service.approvals is not None:
                action = await tx.fetch_value(
                    select(ApprovalActionRow.id).where(
                        ApprovalActionRow.target_type == "opportunity",
                        ApprovalActionRow.target_id == opportunity_id,
                        ApprovalActionRow.action_type == "ADD_OPPORTUNITY_TO_PRODUCTION",
                        ApprovalActionRow.status == "WAITING_FOR_APPROVAL",
                    ).order_by(ApprovalActionRow.requested_at.desc()).limit(1)
                )
                if action is not None:
                    await service.approvals.decide(
                        action_id=action, actor_id=actor_id, decision="REJECT", reason=reason
                    )
                    return await service.get(opportunity_id)
            now = _now()
            await tx.execute(
                update(OpportunityRow).where(OpportunityRow.id == opportunity_id)
                .values(status="REJECTED", rejected_at=now, rejection_reason=reason, updated_at=now)
            )
            await service._status_history(
                opportunity_id, previous, "REJECTED", reason, actor_id, []
            )
            return await service.get(opportunity_id)

    async def get(self, opportunity_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            select(OpportunityRow.__table__).where(OpportunityRow.id == opportunity_id)
        )
        if row is None:
            raise KeyError(f"机会不存在: {opportunity_id}")
        return row

    async def get_detail(self, opportunity_id: str) -> dict[str, Any]:
        opportunity = await self.get(opportunity_id)
        opportunity["observations"] = await self.database.fetch_all(
            select(OpportunityObservationRow.__table__)
            .where(OpportunityObservationRow.opportunity_id == opportunity_id)
            .order_by(OpportunityObservationRow.observed_at.desc())
        )
        opportunity["keywords"] = await self.database.fetch_all(
            select(
                KeywordRow.__table__, OpportunityKeywordRow.keyword_role,
                OpportunityKeywordRow.relevance_score,
            )
            .join_from(KeywordRow, OpportunityKeywordRow,
                       OpportunityKeywordRow.keyword_id == KeywordRow.id)
            .where(OpportunityKeywordRow.opportunity_id == opportunity_id)
            .order_by(
                case((OpportunityKeywordRow.keyword_role == "PRIMARY", 0), else_=1),
                KeywordRow.normalized_keyword,
            )
        )
        opportunity["competitor_pages"] = await self.database.fetch_all(
            select(
                CompetitorPageRow.__table__, OpportunityCompetitorPageRow.gap_type,
                OpportunityCompetitorPageRow.relevance_score,
                OpportunityCompetitorPageRow.evidence_snapshot_id,
            )
            .join_from(
                CompetitorPageRow, OpportunityCompetitorPageRow,
                OpportunityCompetitorPageRow.competitor_page_id == CompetitorPageRow.id,
            )
            .where(OpportunityCompetitorPageRow.opportunity_id == opportunity_id)
            .order_by(CompetitorPageRow.url)
        )
        opportunity["status_history"] = await self.database.fetch_all(
            select(OpportunityStatusHistoryRow.__table__)
            .where(OpportunityStatusHistoryRow.opportunity_id == opportunity_id)
            .order_by(OpportunityStatusHistoryRow.created_at)
        )
        opportunity["approval_actions"] = await self.database.fetch_all(
            select(ApprovalActionRow.__table__).where(
                ApprovalActionRow.target_type == "opportunity",
                ApprovalActionRow.target_id == opportunity_id,
            ).order_by(ApprovalActionRow.requested_at.desc())
        )
        opportunity["tasks"] = await self.database.fetch_all(
            select(
                ExecutionTaskRow.__table__, TaskOpportunityRow.relation_type,
                TaskOpportunityRow.is_primary,
            )
            .join_from(
                ExecutionTaskRow, TaskOpportunityRow,
                TaskOpportunityRow.task_id == ExecutionTaskRow.id,
            )
            .where(TaskOpportunityRow.opportunity_id == opportunity_id)
            .order_by(ExecutionTaskRow.created_at.desc())
        )
        return opportunity

    async def list(
        self,
        site_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = select(OpportunityRow.__table__).where(
            OpportunityRow.site_id == site_id, OpportunityRow.archived_at.is_(None)
        )
        if status:
            query = query.where(OpportunityRow.status == status)
        if priority:
            query = query.where(OpportunityRow.priority_level == priority)
        return await self.database.fetch_all(
            query.order_by(
                OpportunityRow.current_score.desc().nulls_last(),
                OpportunityRow.last_seen_at.desc(),
            ).limit(max(1, min(limit, 500)))
        )

    async def _status_history(
        self,
        opportunity_id: str,
        from_status: str | None,
        to_status: str,
        reason: str,
        actor_id: str,
        triggered: list[str],
    ) -> None:
        await self.database.execute(
            insert(OpportunityStatusHistoryRow).values(
                id=_id("oppstatus"), opportunity_id=opportunity_id, from_status=from_status,
                to_status=to_status, reason=reason, policy_version=self.policy.version,
                threshold_snapshot_json=json.dumps({
                    "high": self.policy.high_threshold,
                    "medium": self.policy.medium_threshold,
                }),
                triggered_conditions_json=json.dumps(triggered, ensure_ascii=False),
                changed_by_actor_id=actor_id, created_at=_now(),
            )
        )
