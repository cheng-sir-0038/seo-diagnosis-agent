from __future__ import annotations

# ruff: noqa: E501
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    ExecutionTaskRow,
    KeywordRow,
    MonitoringCheckRow,
    MonitoringGoalRow,
    MonitoringObservationRow,
    MonitoringOutcomeGoalRow,
    MonitoringOutcomeRow,
    MonitoringPlanRow,
    PublishRecordRow,
    SiteRow,
)


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _parse_time(value: str | None) -> datetime:
    if not value:
        return _now_dt()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class MonitoringService:
    def __init__(
        self,
        database: SeoAgentDatabase,
        *,
        indexing_days: tuple[int, ...] = (3, 7, 14, 28),
        ranking_weeks: int = 8,
        policy_version: str = "v1",
    ) -> None:
        self.database = database
        self.indexing_days = indexing_days
        self.ranking_weeks = ranking_weeks
        self.policy_version = policy_version

    async def create_plan(
        self,
        *,
        publish_record_id: str,
        published_at: str | None,
        origin_trace_id: str | None = None,
        database: SeoAgentDatabase | None = None,
    ) -> dict[str, Any]:
        async with (database or self.database).transaction() as db:
            publication = await db.fetch_value(
                select(PublishRecordRow.id)
                .where(PublishRecordRow.id == publish_record_id)
                .with_for_update()
            )
            if publication is None:
                raise KeyError(f"发布记录不存在: {publish_record_id}")
            existing = await db.fetch_one(
                select(MonitoringPlanRow.__table__).where(
                    MonitoringPlanRow.publish_record_id == publish_record_id
                )
            )
            if existing is not None:
                return existing
            started = _parse_time(published_at)
            plan_id = _id("monitor")
            now = _now()
            await db.execute(
                insert(MonitoringPlanRow).values(
                    id=plan_id,
                    publish_record_id=publish_record_id,
                    status="MONITORING_INDEXING",
                    policy_version=self.policy_version,
                    origin_trace_id=origin_trace_id,
                    indexing_started_at=started.isoformat(),
                    next_check_at=(started + timedelta(days=self.indexing_days[0])).isoformat(),
                    created_at=now,
                    updated_at=now,
                )
            )
            for sequence, day in enumerate(self.indexing_days, start=1):
                scheduled_for = (started + timedelta(days=day)).isoformat()
                await db.execute(
                    insert(MonitoringCheckRow).values(
                        id=_id("check"),
                        monitoring_plan_id=plan_id,
                        check_type="INDEXING",
                        sequence_no=sequence,
                        scheduled_for=scheduled_for,
                        status="PENDING",
                        idempotency_key=f"indexing:{plan_id}:{day}",
                        created_at=now,
                    )
                )
            return await self.get_plan(plan_id, database=db)

    async def get_plan_by_publish(self, publish_record_id: str) -> dict[str, Any] | None:
        return await self.database.fetch_one(
            select(MonitoringPlanRow.__table__)
            .where(MonitoringPlanRow.publish_record_id == publish_record_id)
            .order_by(MonitoringPlanRow.created_at.desc())
            .limit(1)
        )

    async def get_plan(
        self, plan_id: str, *, database: SeoAgentDatabase | None = None, for_update: bool = False
    ) -> dict[str, Any]:
        query = select(MonitoringPlanRow.__table__).where(MonitoringPlanRow.id == plan_id)
        if for_update:
            query = query.with_for_update()
        row = await (database or self.database).fetch_one(query)
        if row is None:
            raise KeyError(f"监控计划不存在: {plan_id}")
        return row

    async def add_goal(
        self,
        plan_id: str,
        *,
        goal_type: str,
        target_keyword: str | None = None,
        target_value: float | None = None,
        comparison_operator: str | None = None,
        minimum_improvement: float | None = None,
        baseline_value: float | None = None,
    ) -> dict[str, Any]:
        await self.get_plan(plan_id)
        goal_id = _id("goal")
        await self.database.execute(
            insert(MonitoringGoalRow).values(
                id=goal_id,
                monitoring_plan_id=plan_id,
                goal_type=goal_type,
                target_keyword=target_keyword,
                target_value=target_value,
                comparison_operator=comparison_operator,
                minimum_improvement=minimum_improvement,
                baseline_value=baseline_value,
                created_at=_now(),
            )
        )
        row = await self.database.fetch_one(
            select(MonitoringGoalRow.__table__).where(MonitoringGoalRow.id == goal_id)
        )
        assert row is not None
        return row

    async def record_indexing_result(
        self,
        check_id: str,
        *,
        indexed: bool,
        evidence_snapshot_id: str | None = None,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.transaction() as db:
            check = await db.fetch_one(
                select(MonitoringCheckRow.__table__)
                .where(
                    MonitoringCheckRow.id == check_id, MonitoringCheckRow.check_type == "INDEXING"
                )
                .with_for_update()
            )
            if check is None:
                raise KeyError(f"收录检查不存在: {check_id}")
            plan = await self.get_plan(check["monitoring_plan_id"], database=db, for_update=True)
            if check["status"] == "COMPLETED":
                return plan
            observed = observed_at or _now()
            await db.execute(
                insert(MonitoringObservationRow).values(
                    id=_id("observation"),
                    monitoring_check_id=check_id,
                    metric_type="INDEXED",
                    value_numeric=1.0 if indexed else 0.0,
                    value_text="indexed" if indexed else "not_indexed",
                    evidence_snapshot_id=evidence_snapshot_id,
                    observed_at=observed,
                    created_at=_now(),
                )
            )
            await db.execute(
                update(MonitoringCheckRow)
                .where(MonitoringCheckRow.id == check_id)
                .values(
                    status="COMPLETED",
                    started_at=func.coalesce(MonitoringCheckRow.started_at, observed),
                    completed_at=observed,
                    conclusion="INDEXED" if indexed else "NOT_INDEXED",
                )
            )
            if indexed:
                indexed_at = _parse_time(observed)
                ranking_end = indexed_at + timedelta(weeks=self.ranking_weeks)
                await db.execute(
                    update(MonitoringPlanRow)
                    .where(MonitoringPlanRow.id == plan["id"])
                    .values(
                        status="MONITORING_RANKING",
                        indexed_at=indexed_at.isoformat(),
                        ranking_started_at=indexed_at.isoformat(),
                        ranking_ends_at=ranking_end.isoformat(),
                        next_check_at=(indexed_at + timedelta(weeks=1)).isoformat(),
                        updated_at=_now(),
                    )
                )
                return await self.get_plan(plan["id"], database=db)
            pending = await db.fetch_all(
                select(MonitoringCheckRow.__table__)
                .where(
                    MonitoringCheckRow.monitoring_plan_id == plan["id"],
                    MonitoringCheckRow.check_type == "INDEXING",
                    MonitoringCheckRow.status == "PENDING",
                )
                .order_by(MonitoringCheckRow.sequence_no)
            )
            if pending:
                await db.execute(
                    update(MonitoringPlanRow)
                    .where(MonitoringPlanRow.id == plan["id"])
                    .values(
                        next_check_at=pending[0]["scheduled_for"],
                        updated_at=_now(),
                    )
                )
            else:
                await db.execute(
                    update(MonitoringPlanRow)
                    .where(MonitoringPlanRow.id == plan["id"])
                    .values(
                        status="INDEXING_INCONCLUSIVE",
                        final_outcome="INCONCLUSIVE",
                        next_check_at=None,
                        completed_at=_now(),
                        updated_at=_now(),
                    )
                )
            return await self.get_plan(plan["id"], database=db)

    async def create_ranking_check(
        self,
        plan_id: str,
        *,
        sequence_no: int,
        scheduled_for: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.transaction() as db:
            plan = await self.get_plan(plan_id, database=db, for_update=True)
            when = scheduled_for or _now()
            key = f"ranking:{plan_id}:{sequence_no}"
            existing = await db.fetch_one(
                select(MonitoringCheckRow.__table__).where(
                    MonitoringCheckRow.idempotency_key == key
                )
            )
            if existing is not None:
                return existing
            check_id = _id("check")
            await db.execute(
                insert(MonitoringCheckRow).values(
                    id=check_id,
                    monitoring_plan_id=plan["id"],
                    check_type="RANKING",
                    sequence_no=sequence_no,
                    scheduled_for=when,
                    status="PENDING",
                    idempotency_key=key,
                    created_at=_now(),
                )
            )
            row = await db.fetch_one(
                select(MonitoringCheckRow.__table__).where(MonitoringCheckRow.id == check_id)
            )
            assert row is not None
            return row

    async def record_ranking_observation(
        self,
        check_id: str,
        *,
        keyword: str,
        position: float,
        evidence_snapshot_id: str | None = None,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.transaction() as db:
            check = await db.fetch_one(
                select(MonitoringCheckRow.__table__, MonitoringPlanRow.publish_record_id)
                .join(
                    MonitoringPlanRow, MonitoringPlanRow.id == MonitoringCheckRow.monitoring_plan_id
                )
                .where(
                    MonitoringCheckRow.id == check_id, MonitoringCheckRow.check_type == "RANKING"
                )
            )
            if check is None:
                raise KeyError(f"排名检查不存在: {check_id}")
            plan = await self.get_plan(check["monitoring_plan_id"], database=db, for_update=True)
            keyword_id = await self._get_or_create_keyword(db, keyword, check["publish_record_id"])
            previous = await db.fetch_one(
                select(MonitoringObservationRow.value_numeric)
                .join(
                    MonitoringCheckRow,
                    MonitoringCheckRow.id == MonitoringObservationRow.monitoring_check_id,
                )
                .where(
                    MonitoringCheckRow.monitoring_plan_id == check["monitoring_plan_id"],
                    MonitoringObservationRow.metric_type == "RANK_POSITION",
                    MonitoringObservationRow.keyword_id == keyword_id,
                )
                .order_by(MonitoringObservationRow.observed_at.desc())
                .limit(1)
            )
            observed = observed_at or _now()
            observation_id = _id("observation")
            previous_value = previous["value_numeric"] if previous else None
            await db.execute(
                insert(MonitoringObservationRow).values(
                    id=observation_id,
                    monitoring_check_id=check_id,
                    metric_type="RANK_POSITION",
                    keyword_id=keyword_id,
                    value_numeric=position,
                    previous_value_numeric=previous_value,
                    change_value=(previous_value - position)
                    if previous_value is not None
                    else None,
                    evidence_snapshot_id=evidence_snapshot_id,
                    observed_at=observed,
                    created_at=_now(),
                )
            )
            await db.execute(
                update(MonitoringCheckRow)
                .where(MonitoringCheckRow.id == check_id)
                .values(
                    status="COMPLETED",
                    started_at=func.coalesce(MonitoringCheckRow.started_at, observed),
                    completed_at=observed,
                    conclusion=f"{keyword}: {position}",
                )
            )
            next_check = _parse_time(observed) + timedelta(weeks=1)
            if plan["ranking_ends_at"] and next_check <= _parse_time(plan["ranking_ends_at"]):
                await db.execute(
                    update(MonitoringPlanRow)
                    .where(MonitoringPlanRow.id == plan["id"])
                    .values(
                        next_check_at=next_check.isoformat(),
                        updated_at=_now(),
                    )
                )
            row = await db.fetch_one(
                select(MonitoringObservationRow.__table__).where(
                    MonitoringObservationRow.id == observation_id
                )
            )
            assert row is not None
            return row

    async def evaluate_plan(self, plan_id: str) -> dict[str, Any]:
        async with self.database.transaction() as db:
            await self.get_plan(plan_id, database=db, for_update=True)
            goals = await db.fetch_all(
                select(MonitoringGoalRow.__table__)
                .where(MonitoringGoalRow.monitoring_plan_id == plan_id)
                .order_by(MonitoringGoalRow.created_at)
            )
            outcomes: list[tuple[dict[str, Any], str, float | None, str]] = []
            for goal in goals:
                actual = await self._actual_value(db, plan_id, goal)
                status, explanation = self._evaluate_goal(goal, actual)
                outcomes.append((goal, status, actual, explanation))
            statuses = [item[1] for item in outcomes]
            if not goals or all(status == "INSUFFICIENT_DATA" for status in statuses):
                final = "INCONCLUSIVE"
                confidence = "LOW"
            elif all(status == "ACHIEVED" for status in statuses):
                final = "EFFECTIVE"
                confidence = "HIGH"
            elif any(status in {"ACHIEVED", "PARTIALLY_ACHIEVED"} for status in statuses):
                final = "PARTIALLY_EFFECTIVE"
                confidence = "MEDIUM"
            else:
                final = "INEFFECTIVE"
                confidence = "MEDIUM"
            outcome_id = _id("outcome")
            now = _now()
            await db.execute(
                insert(MonitoringOutcomeRow).values(
                    id=outcome_id,
                    monitoring_plan_id=plan_id,
                    outcome=final,
                    confidence=confidence,
                    summary=f"{len(goals)} 个目标评价完成",
                    created_at=now,
                )
            )
            for goal, status, actual, explanation in outcomes:
                target = goal["target_value"]
                improvement = None
                baseline = goal["baseline_value"]
                if baseline not in (None, 0) and actual is not None:
                    improvement = ((actual - baseline) / abs(baseline)) * 100
                await db.execute(
                    insert(MonitoringOutcomeGoalRow).values(
                        id=_id("outcomegoal"),
                        monitoring_outcome_id=outcome_id,
                        monitoring_goal_id=goal["id"],
                        status=status,
                        actual_value=actual,
                        target_value_snapshot=target,
                        improvement_percent=improvement,
                        explanation=explanation,
                        created_at=now,
                    )
                )
            await db.execute(
                update(MonitoringPlanRow)
                .where(MonitoringPlanRow.id == plan_id)
                .values(
                    status="COMPLETED",
                    final_outcome=final,
                    completed_at=now,
                    next_check_at=None,
                    updated_at=now,
                )
            )
            row = await db.fetch_one(
                select(MonitoringOutcomeRow.__table__).where(MonitoringOutcomeRow.id == outcome_id)
            )
            assert row is not None
            return row

    async def _get_or_create_keyword(
        self, db: SeoAgentDatabase, keyword: str, publish_record_id: str
    ) -> str:
        site = await db.fetch_one(
            select(SiteRow.default_language, SiteRow.default_country)
            .select_from(PublishRecordRow)
            .join(ExecutionTaskRow, ExecutionTaskRow.id == PublishRecordRow.task_id)
            .join(SiteRow, SiteRow.id == ExecutionTaskRow.site_id)
            .where(PublishRecordRow.id == publish_record_id)
        )
        if site is None:
            raise KeyError(f"发布记录不存在: {publish_record_id}")
        normalized = keyword.strip().lower()
        language, country = site["default_language"], site["default_country"]
        now = _now()
        await db.execute(
            pg_insert(KeywordRow)
            .values(
                id=_id("keyword"),
                normalized_keyword=normalized,
                language=language,
                country=country,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    KeywordRow.normalized_keyword,
                    KeywordRow.language,
                    KeywordRow.country,
                ]
            )
        )
        return await db.fetch_value(
            select(KeywordRow.id).where(
                KeywordRow.normalized_keyword == normalized,
                KeywordRow.language == language,
                KeywordRow.country == country,
            )
        )

    async def _actual_value(
        self, db: SeoAgentDatabase, plan_id: str, goal: dict[str, Any]
    ) -> float | None:
        if goal["goal_type"] in {"TOP100", "TOP20", "TOP10"}:
            query = (
                select(func.min(MonitoringObservationRow.value_numeric))
                .join(
                    MonitoringCheckRow,
                    MonitoringCheckRow.id == MonitoringObservationRow.monitoring_check_id,
                )
                .where(
                    MonitoringCheckRow.monitoring_plan_id == plan_id,
                    MonitoringObservationRow.metric_type == "RANK_POSITION",
                )
            )
            if goal["target_keyword"] is not None:
                query = query.join(
                    KeywordRow, KeywordRow.id == MonitoringObservationRow.keyword_id
                ).where(KeywordRow.normalized_keyword == goal["target_keyword"].lower())
            return await db.fetch_value(query)
        return await db.fetch_value(
            select(func.max(MonitoringObservationRow.value_numeric))
            .join(
                MonitoringCheckRow,
                MonitoringCheckRow.id == MonitoringObservationRow.monitoring_check_id,
            )
            .where(
                MonitoringCheckRow.monitoring_plan_id == plan_id,
                MonitoringObservationRow.metric_type == goal["goal_type"],
            )
        )

    @staticmethod
    def _evaluate_goal(goal: dict[str, Any], actual: float | None) -> tuple[str, str]:
        if actual is None:
            return "INSUFFICIENT_DATA", "没有可用观测"
        default_targets = {"TOP100": 100.0, "TOP20": 20.0, "TOP10": 10.0}
        target = goal["target_value"]
        if target is None:
            target = default_targets.get(goal["goal_type"])
        operator = goal["comparison_operator"] or (
            "<=" if goal["goal_type"].startswith("TOP") else ">="
        )
        achieved = {
            "<=": actual <= target,
            ">=": actual >= target,
            "<": actual < target,
            ">": actual > target,
            "=": actual == target,
            "==": actual == target,
        }.get(operator)
        if achieved is None:
            raise ValueError(f"不支持的比较操作符: {operator}")
        return (
            ("ACHIEVED", f"实际值 {actual} 满足 {operator} {target}")
            if achieved
            else ("NOT_ACHIEVED", f"实际值 {actual} 未满足 {operator} {target}")
        )
