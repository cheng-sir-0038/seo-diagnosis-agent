from __future__ import annotations

# ruff: noqa: E501
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiosqlite

from src.seo_agent.approvals.policy import ActionPolicy
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.opportunities.models import OpportunityCandidate
from src.seo_agent.opportunities.normalizer import OpportunityCandidateNormalizer
from src.seo_agent.opportunities.policy import OpportunityPolicy
from src.seo_agent.services.system import SeoAgentSystemService


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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
        existing_key = await self.database.fetch_one(
            "SELECT * FROM schedule_runs WHERE idempotency_key = ?", (idempotency_key,)
        )
        if existing_key is not None:
            return existing_key
        running = await self.database.fetch_one(
            "SELECT * FROM schedule_runs WHERE schedule_id = ? AND status = 'RUNNING' "
            "ORDER BY started_at DESC LIMIT 1",
            (schedule_id,),
        )
        if running is not None:
            if trigger_type.upper() == "MANUAL":
                return running
            return await self._record_skipped(
                schedule_id=schedule_id,
                trigger_type=trigger_type,
                idempotency_key=idempotency_key,
                existing_run_id=running["id"],
                scheduled_at=scheduled_at,
            )
        run_id = _id("scan")
        now = _now()
        try:
            await self.database.execute(
                "INSERT INTO schedule_runs(id, schedule_id, trigger_type, scheduled_at, started_at, "
                "status, idempotency_key, created_at) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?)",
                (
                    run_id,
                    schedule_id,
                    trigger_type.upper(),
                    scheduled_at,
                    now,
                    idempotency_key,
                    now,
                ),
            )
        except aiosqlite.IntegrityError:
            concurrent = await self.database.fetch_one(
                "SELECT * FROM schedule_runs WHERE schedule_id = ? AND status = 'RUNNING' "
                "ORDER BY started_at DESC LIMIT 1",
                (schedule_id,),
            )
            if concurrent is None:
                raise
            if trigger_type.upper() == "MANUAL":
                return concurrent
            return await self._record_skipped(
                schedule_id=schedule_id,
                trigger_type=trigger_type,
                idempotency_key=idempotency_key,
                existing_run_id=concurrent["id"],
                scheduled_at=scheduled_at,
            )
        row = await self.get(run_id)
        return row

    async def _record_skipped(
        self,
        *,
        schedule_id: str,
        trigger_type: str,
        idempotency_key: str,
        existing_run_id: str,
        scheduled_at: str | None,
    ) -> dict[str, Any]:
        run_id = _id("scan")
        now = _now()
        await self.database.execute(
            "INSERT INTO schedule_runs(id, schedule_id, trigger_type, scheduled_at, completed_at, "
            "status, skip_reason, existing_run_id, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'SKIPPED_ALREADY_RUNNING', 'already_running', ?, ?, ?)",
            (
                run_id,
                schedule_id,
                trigger_type.upper(),
                scheduled_at,
                now,
                existing_run_id,
                idempotency_key,
                now,
            ),
        )
        return await self.get(run_id)

    async def get(self, run_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one("SELECT * FROM schedule_runs WHERE id = ?", (run_id,))
        if row is None:
            raise KeyError(f"扫描运行不存在: {run_id}")
        return row

    async def complete(self, run_id: str, *, status: str = "COMPLETED") -> dict[str, Any]:
        run = await self.get(run_id)
        now = _now()
        await self.database.execute(
            "UPDATE schedule_runs SET status = ?, completed_at = ? WHERE id = ?",
            (status, now, run_id),
        )
        await self.database.execute(
            "UPDATE work_schedules SET last_run_at = ?, updated_at = ? WHERE id = ?",
            (now, now, run["schedule_id"]),
        )
        return await self.get(run_id)


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
        candidate = self.normalizer.normalize(candidate)
        journal = await self.database.fetch_one(
            "SELECT payload_json FROM work_records WHERE idempotency_key = ?", (idempotency_key,)
        )
        if journal is not None:
            payload = json.loads(journal["payload_json"])
            return await self.get(payload["opportunity_id"])
        fingerprint = self.fingerprint(site_id, candidate)
        existing = await self.database.fetch_one(
            "SELECT * FROM opportunities WHERE site_id = ? AND fingerprint = ?",
            (site_id, fingerprint),
        )
        previous_observation = None
        if existing is not None:
            previous_observation = await self.database.fetch_one(
                "SELECT * FROM opportunity_observations WHERE opportunity_id = ? "
                "ORDER BY observed_at DESC LIMIT 1",
                (existing["id"],),
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
                "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, "
                "opportunity_type, target_country, target_language, title, current_score, "
                "priority_level, status, current_summary, policy_version, first_seen_at, "
                "last_seen_at, seen_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    opportunity_id,
                    site_id,
                    fingerprint,
                    candidate.normalized_intent,
                    candidate.opportunity_type.upper(),
                    candidate.target_country.upper(),
                    candidate.target_language.lower(),
                    candidate.title,
                    score.total,
                    score.priority,
                    priority_status,
                    candidate.summary,
                    self.policy.version,
                    now,
                    now,
                    now,
                    now,
                ),
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
                "UPDATE opportunities SET title = ?, current_score = ?, priority_level = ?, "
                "status = ?, current_summary = ?, policy_version = ?, last_seen_at = ?, "
                "seen_count = seen_count + 1, reopened_at = CASE WHEN ? = 1 THEN ? ELSE reopened_at END, "
                "updated_at = ? WHERE id = ?",
                (
                    candidate.title,
                    score.total,
                    score.priority,
                    target_status,
                    candidate.summary,
                    self.policy.version,
                    now,
                    int(reopened),
                    now,
                    now,
                    opportunity_id,
                ),
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
            "INSERT INTO opportunity_observations(id, opportunity_id, score, priority_level, "
            "search_volume, keyword_difficulty, competitor_count, gap_summary, score_factors_json, "
            "policy_version, threshold_snapshot_json, observed_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_id,
                opportunity_id,
                score.total,
                score.priority,
                candidate.search_volume,
                candidate.keyword_difficulty,
                candidate.competitor_count,
                candidate.summary,
                score.factors.model_dump_json(),
                self.policy.version,
                json.dumps(
                    {
                        "high": self.policy.high_threshold,
                        "medium": self.policy.medium_threshold,
                        "reopen_score": self.policy.reopen_score_increase,
                        "reopen_volume_percent": self.policy.reopen_search_volume_growth_percent,
                    }
                ),
                now,
                now,
            ),
        )
        for evidence_id in candidate.evidence_snapshot_ids:
            await self.database.execute(
                "INSERT OR IGNORE INTO opportunity_observation_evidence(id, observation_id, "
                "evidence_snapshot_id, evidence_role, contribution_weight, created_at) "
                "VALUES (?, ?, ?, 'SCORE_INPUT', 1.0, ?)",
                (_id("oppevidence"), observation_id, evidence_id, now),
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
        for index, keyword in enumerate(candidate.keywords):
            row = await self.database.fetch_one(
                "SELECT * FROM keywords WHERE normalized_keyword = ? AND language = ? AND country = ?",
                (keyword, candidate.target_language.lower(), candidate.target_country.upper()),
            )
            if row is None:
                keyword_id = _id("keyword")
                await self.database.execute(
                    "INSERT INTO keywords(id, normalized_keyword, language, country, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        keyword_id,
                        keyword,
                        candidate.target_language.lower(),
                        candidate.target_country.upper(),
                        now,
                        now,
                    ),
                )
            else:
                keyword_id = row["id"]
            await self.database.execute(
                "INSERT OR IGNORE INTO opportunity_keywords(id, opportunity_id, keyword_id, "
                "keyword_role, relevance_score, first_seen_at, last_seen_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _id("oppkeyword"),
                    opportunity_id,
                    keyword_id,
                    "PRIMARY" if index == 0 else "SECONDARY",
                    1.0 if index == 0 else 0.8,
                    now,
                    now,
                    now,
                ),
            )
            await self.database.execute(
                "UPDATE opportunity_keywords SET last_seen_at = ? WHERE opportunity_id = ? "
                "AND keyword_id = ?",
                (now, opportunity_id, keyword_id),
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
            row = await self.database.fetch_one(
                "SELECT * FROM competitor_pages WHERE site_id = ? AND url = ?",
                (site_id, url),
            )
            if row is None:
                page_id = _id("competitor_page")
                await self.database.execute(
                    "INSERT INTO competitor_pages(id, site_id, competitor_domain, url, "
                    "first_seen_at, last_seen_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (page_id, site_id, domain, url, now, now, now, now),
                )
            else:
                page_id = row["id"]
                await self.database.execute(
                    "UPDATE competitor_pages SET last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, page_id),
                )
            await self.database.execute(
                "INSERT OR IGNORE INTO opportunity_competitor_pages(id, opportunity_id, "
                "competitor_page_id, gap_type, relevance_score, evidence_snapshot_id, "
                "created_at) VALUES (?, ?, ?, ?, 1.0, ?, ?)",
                (
                    _id("opp_competitor"),
                    opportunity_id,
                    page_id,
                    candidate.opportunity_type,
                    evidence_snapshot_id,
                    now,
                ),
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
        active = await self.database.fetch_one(
            "SELECT * FROM approval_actions WHERE target_type = 'opportunity' AND target_id = ? "
            "AND status = 'WAITING_FOR_APPROVAL' ORDER BY requested_at DESC LIMIT 1",
            (opportunity["id"],),
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
        opportunity = await self.get(opportunity_id)
        now = _now()
        await self.database.execute(
            "UPDATE opportunities SET status = 'REJECTED', rejected_at = ?, rejection_reason = ?, "
            "updated_at = ? WHERE id = ?",
            (now, reason, now, opportunity_id),
        )
        await self._status_history(
            opportunity_id,
            opportunity["status"],
            "REJECTED",
            reason,
            actor_id,
            [],
        )
        return await self.get(opportunity_id)

    async def get(self, opportunity_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)
        )
        if row is None:
            raise KeyError(f"机会不存在: {opportunity_id}")
        return row

    async def get_detail(self, opportunity_id: str) -> dict[str, Any]:
        opportunity = await self.get(opportunity_id)
        opportunity["observations"] = await self.database.fetch_all(
            "SELECT * FROM opportunity_observations WHERE opportunity_id = ? "
            "ORDER BY observed_at DESC",
            (opportunity_id,),
        )
        opportunity["keywords"] = await self.database.fetch_all(
            "SELECT k.*, r.keyword_role, r.relevance_score FROM opportunity_keywords r "
            "JOIN keywords k ON k.id = r.keyword_id WHERE r.opportunity_id = ? "
            "ORDER BY CASE r.keyword_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, k.normalized_keyword",
            (opportunity_id,),
        )
        opportunity["competitor_pages"] = await self.database.fetch_all(
            "SELECT p.*, r.gap_type, r.relevance_score, r.evidence_snapshot_id "
            "FROM opportunity_competitor_pages r "
            "JOIN competitor_pages p ON p.id = r.competitor_page_id "
            "WHERE r.opportunity_id = ? ORDER BY p.url",
            (opportunity_id,),
        )
        opportunity["status_history"] = await self.database.fetch_all(
            "SELECT * FROM opportunity_status_history WHERE opportunity_id = ? ORDER BY created_at",
            (opportunity_id,),
        )
        opportunity["approval_actions"] = await self.database.fetch_all(
            "SELECT * FROM approval_actions WHERE target_type = 'opportunity' AND target_id = ? "
            "ORDER BY requested_at DESC",
            (opportunity_id,),
        )
        opportunity["tasks"] = await self.database.fetch_all(
            "SELECT t.*, r.relation_type, r.is_primary FROM task_opportunities r "
            "JOIN execution_tasks t ON t.id = r.task_id WHERE r.opportunity_id = ? "
            "ORDER BY t.created_at DESC",
            (opportunity_id,),
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
        clauses = ["site_id = ?", "archived_at IS NULL"]
        params: list[Any] = [site_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if priority:
            clauses.append("priority_level = ?")
            params.append(priority)
        params.append(max(1, min(limit, 500)))
        return await self.database.fetch_all(
            f"SELECT * FROM opportunities WHERE {' AND '.join(clauses)} "
            "ORDER BY current_score DESC, last_seen_at DESC LIMIT ?",
            tuple(params),
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
            "INSERT INTO opportunity_status_history(id, opportunity_id, from_status, to_status, "
            "reason, policy_version, threshold_snapshot_json, triggered_conditions_json, "
            "changed_by_actor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _id("oppstatus"),
                opportunity_id,
                from_status,
                to_status,
                reason,
                self.policy.version,
                json.dumps(
                    {
                        "high": self.policy.high_threshold,
                        "medium": self.policy.medium_threshold,
                    }
                ),
                json.dumps(triggered, ensure_ascii=False),
                actor_id,
                _now(),
            ),
        )
