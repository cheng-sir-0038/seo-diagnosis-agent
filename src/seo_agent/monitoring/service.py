from __future__ import annotations

# ruff: noqa: E501
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from src.seo_agent.db.database import SeoAgentDatabase


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
    ) -> dict[str, Any]:
        existing = await self.get_plan_by_publish(publish_record_id)
        if existing is not None:
            return existing
        started = _parse_time(published_at)
        plan_id = _id("monitor")
        now = _now()
        await self.database.execute(
            "INSERT INTO monitoring_plans(id, publish_record_id, status, policy_version, "
            "origin_trace_id, indexing_started_at, next_check_at, created_at, updated_at) "
            "VALUES (?, ?, 'MONITORING_INDEXING', ?, ?, ?, ?, ?, ?)",
            (
                plan_id,
                publish_record_id,
                self.policy_version,
                origin_trace_id,
                started.isoformat(),
                (started + timedelta(days=self.indexing_days[0])).isoformat(),
                now,
                now,
            ),
        )
        for sequence, day in enumerate(self.indexing_days, start=1):
            scheduled_for = (started + timedelta(days=day)).isoformat()
            await self.database.execute(
                "INSERT INTO monitoring_checks(id, monitoring_plan_id, check_type, sequence_no, "
                "scheduled_for, status, idempotency_key, created_at) "
                "VALUES (?, ?, 'INDEXING', ?, ?, 'PENDING', ?, ?)",
                (
                    _id("check"),
                    plan_id,
                    sequence,
                    scheduled_for,
                    f"indexing:{plan_id}:{day}",
                    now,
                ),
            )
        plan = await self.get_plan_by_publish(publish_record_id)
        assert plan is not None
        return plan

    async def get_plan_by_publish(self, publish_record_id: str) -> dict[str, Any] | None:
        return await self.database.fetch_one(
            "SELECT * FROM monitoring_plans WHERE publish_record_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (publish_record_id,),
        )

    async def get_plan(self, plan_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM monitoring_plans WHERE id = ?", (plan_id,)
        )
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
            "INSERT INTO monitoring_goals(id, monitoring_plan_id, goal_type, target_keyword, "
            "target_value, comparison_operator, minimum_improvement, baseline_value, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                goal_id,
                plan_id,
                goal_type,
                target_keyword,
                target_value,
                comparison_operator,
                minimum_improvement,
                baseline_value,
                _now(),
            ),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM monitoring_goals WHERE id = ?", (goal_id,)
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
        check = await self.database.fetch_one(
            "SELECT * FROM monitoring_checks WHERE id = ? AND check_type = 'INDEXING'",
            (check_id,),
        )
        if check is None:
            raise KeyError(f"收录检查不存在: {check_id}")
        observed = observed_at or _now()
        await self.database.execute(
            "INSERT INTO monitoring_observations(id, monitoring_check_id, metric_type, "
            "value_numeric, value_text, evidence_snapshot_id, observed_at, created_at) "
            "VALUES (?, ?, 'INDEXED', ?, ?, ?, ?, ?)",
            (
                _id("observation"),
                check_id,
                1.0 if indexed else 0.0,
                "indexed" if indexed else "not_indexed",
                evidence_snapshot_id,
                observed,
                _now(),
            ),
        )
        await self.database.execute(
            "UPDATE monitoring_checks SET status = 'COMPLETED', started_at = COALESCE(started_at, ?), "
            "completed_at = ?, conclusion = ? WHERE id = ?",
            (observed, observed, "INDEXED" if indexed else "NOT_INDEXED", check_id),
        )
        plan = await self.get_plan(check["monitoring_plan_id"])
        if indexed:
            indexed_at = _parse_time(observed)
            ranking_end = indexed_at + timedelta(weeks=self.ranking_weeks)
            await self.database.execute(
                "UPDATE monitoring_plans SET status = 'MONITORING_RANKING', indexed_at = ?, "
                "ranking_started_at = ?, ranking_ends_at = ?, next_check_at = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    indexed_at.isoformat(),
                    indexed_at.isoformat(),
                    ranking_end.isoformat(),
                    (indexed_at + timedelta(weeks=1)).isoformat(),
                    _now(),
                    plan["id"],
                ),
            )
            return await self.get_plan(plan["id"])
        pending = await self.database.fetch_all(
            "SELECT * FROM monitoring_checks WHERE monitoring_plan_id = ? AND check_type = "
            "'INDEXING' AND status = 'PENDING' ORDER BY sequence_no",
            (plan["id"],),
        )
        if pending:
            await self.database.execute(
                "UPDATE monitoring_plans SET next_check_at = ?, updated_at = ? WHERE id = ?",
                (pending[0]["scheduled_for"], _now(), plan["id"]),
            )
        else:
            await self.database.execute(
                "UPDATE monitoring_plans SET status = 'INDEXING_INCONCLUSIVE', "
                "final_outcome = 'INCONCLUSIVE', next_check_at = NULL, completed_at = ?, "
                "updated_at = ? WHERE id = ?",
                (_now(), _now(), plan["id"]),
            )
        return await self.get_plan(plan["id"])

    async def create_ranking_check(
        self,
        plan_id: str,
        *,
        sequence_no: int,
        scheduled_for: str | None = None,
    ) -> dict[str, Any]:
        plan = await self.get_plan(plan_id)
        when = scheduled_for or _now()
        key = f"ranking:{plan_id}:{sequence_no}"
        existing = await self.database.fetch_one(
            "SELECT * FROM monitoring_checks WHERE idempotency_key = ?", (key,)
        )
        if existing is not None:
            return existing
        check_id = _id("check")
        await self.database.execute(
            "INSERT INTO monitoring_checks(id, monitoring_plan_id, check_type, sequence_no, "
            "scheduled_for, status, idempotency_key, created_at) "
            "VALUES (?, ?, 'RANKING', ?, ?, 'PENDING', ?, ?)",
            (check_id, plan["id"], sequence_no, when, key, _now()),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM monitoring_checks WHERE id = ?", (check_id,)
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
        check = await self.database.fetch_one(
            "SELECT c.*, p.publish_record_id FROM monitoring_checks c "
            "JOIN monitoring_plans p ON p.id = c.monitoring_plan_id "
            "WHERE c.id = ? AND c.check_type = 'RANKING'",
            (check_id,),
        )
        if check is None:
            raise KeyError(f"排名检查不存在: {check_id}")
        keyword_id = await self._get_or_create_keyword(keyword, check["publish_record_id"])
        previous = await self.database.fetch_one(
            "SELECT o.value_numeric FROM monitoring_observations o "
            "JOIN monitoring_checks c ON c.id = o.monitoring_check_id "
            "WHERE c.monitoring_plan_id = ? AND o.metric_type = 'RANK_POSITION' "
            "AND o.keyword_id = ? ORDER BY o.observed_at DESC LIMIT 1",
            (check["monitoring_plan_id"], keyword_id),
        )
        observed = observed_at or _now()
        observation_id = _id("observation")
        previous_value = previous["value_numeric"] if previous else None
        await self.database.execute(
            "INSERT INTO monitoring_observations(id, monitoring_check_id, metric_type, keyword_id, "
            "value_numeric, previous_value_numeric, change_value, evidence_snapshot_id, observed_at, "
            "created_at) VALUES (?, ?, 'RANK_POSITION', ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_id,
                check_id,
                keyword_id,
                position,
                previous_value,
                (previous_value - position) if previous_value is not None else None,
                evidence_snapshot_id,
                observed,
                _now(),
            ),
        )
        await self.database.execute(
            "UPDATE monitoring_checks SET status = 'COMPLETED', started_at = COALESCE(started_at, ?), "
            "completed_at = ?, conclusion = ? WHERE id = ?",
            (observed, observed, f"{keyword}: {position}", check_id),
        )
        plan = await self.get_plan(check["monitoring_plan_id"])
        next_check = _parse_time(observed) + timedelta(weeks=1)
        if plan["ranking_ends_at"] and next_check <= _parse_time(plan["ranking_ends_at"]):
            await self.database.execute(
                "UPDATE monitoring_plans SET next_check_at = ?, updated_at = ? WHERE id = ?",
                (next_check.isoformat(), _now(), plan["id"]),
            )
        row = await self.database.fetch_one(
            "SELECT * FROM monitoring_observations WHERE id = ?", (observation_id,)
        )
        assert row is not None
        return row

    async def evaluate_plan(self, plan_id: str) -> dict[str, Any]:
        await self.get_plan(plan_id)
        goals = await self.database.fetch_all(
            "SELECT * FROM monitoring_goals WHERE monitoring_plan_id = ? ORDER BY created_at",
            (plan_id,),
        )
        outcomes: list[tuple[dict[str, Any], str, float | None, str]] = []
        for goal in goals:
            actual = await self._actual_value(plan_id, goal)
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
        await self.database.execute(
            "INSERT INTO monitoring_outcomes(id, monitoring_plan_id, outcome, confidence, summary, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (outcome_id, plan_id, final, confidence, f"{len(goals)} 个目标评价完成", now),
        )
        for goal, status, actual, explanation in outcomes:
            target = goal["target_value"]
            improvement = None
            baseline = goal["baseline_value"]
            if baseline not in (None, 0) and actual is not None:
                improvement = ((actual - baseline) / abs(baseline)) * 100
            await self.database.execute(
                "INSERT INTO monitoring_outcome_goals(id, monitoring_outcome_id, monitoring_goal_id, "
                "status, actual_value, target_value_snapshot, improvement_percent, explanation, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _id("outcomegoal"),
                    outcome_id,
                    goal["id"],
                    status,
                    actual,
                    target,
                    improvement,
                    explanation,
                    now,
                ),
            )
        await self.database.execute(
            "UPDATE monitoring_plans SET status = 'COMPLETED', final_outcome = ?, "
            "completed_at = ?, next_check_at = NULL, updated_at = ? WHERE id = ?",
            (final, now, now, plan_id),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM monitoring_outcomes WHERE id = ?", (outcome_id,)
        )
        assert row is not None
        return row

    async def _get_or_create_keyword(self, keyword: str, publish_record_id: str) -> str:
        row = await self.database.fetch_one(
            "SELECT k.id FROM keywords k WHERE k.normalized_keyword = ? LIMIT 1",
            (keyword.strip().lower(),),
        )
        if row is not None:
            return str(row["id"])
        site = await self.database.fetch_one(
            "SELECT s.default_language, s.default_country FROM publish_records p "
            "JOIN execution_tasks t ON t.id = p.task_id JOIN sites s ON s.id = t.site_id "
            "WHERE p.id = ?",
            (publish_record_id,),
        )
        keyword_id = _id("keyword")
        language = site["default_language"] if site else "und"
        country = site["default_country"] if site else "GLOBAL"
        now = _now()
        await self.database.execute(
            "INSERT INTO keywords(id, normalized_keyword, language, country, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (keyword_id, keyword.strip().lower(), language, country, now, now),
        )
        return keyword_id

    async def _actual_value(self, plan_id: str, goal: dict[str, Any]) -> float | None:
        if goal["goal_type"] in {"TOP100", "TOP20", "TOP10"}:
            row = await self.database.fetch_one(
                "SELECT MIN(o.value_numeric) AS best_position FROM monitoring_observations o "
                "JOIN monitoring_checks c ON c.id = o.monitoring_check_id "
                "LEFT JOIN keywords k ON k.id = o.keyword_id "
                "WHERE c.monitoring_plan_id = ? AND o.metric_type = 'RANK_POSITION' "
                "AND (? IS NULL OR k.normalized_keyword = lower(?))",
                (plan_id, goal["target_keyword"], goal["target_keyword"]),
            )
            return row["best_position"] if row and row["best_position"] is not None else None
        row = await self.database.fetch_one(
            "SELECT MAX(o.value_numeric) AS metric FROM monitoring_observations o "
            "JOIN monitoring_checks c ON c.id = o.monitoring_check_id "
            "WHERE c.monitoring_plan_id = ? AND o.metric_type = ?",
            (plan_id, goal["goal_type"]),
        )
        return row["metric"] if row and row["metric"] is not None else None

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
