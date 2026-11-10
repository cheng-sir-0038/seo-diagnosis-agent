from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from src.observability.storage import ObservabilityDatabase
from src.promptops.evaluation import EvaluationService, PromptExecutor
from src.promptops.registry import PromptRegistry


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExperimentService:
    def __init__(
        self,
        database: ObservabilityDatabase,
        registry: PromptRegistry,
        evaluator: EvaluationService,
    ) -> None:
        self.database = database
        self.registry = registry
        self.evaluator = evaluator

    async def create(
        self,
        *,
        prompt_id: str,
        baseline_version_id: str,
        challenger_version_id: str,
        dataset_id: str,
        success_criteria: dict[str, Any],
    ) -> dict[str, Any]:
        experiment_id = _id("experiment")
        now = _now()
        await self.database.execute(
            "INSERT INTO prompt_experiments(id, prompt_id, baseline_version_id, "
            "challenger_version_id, dataset_id, status, success_criteria_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'CREATED', ?, ?)",
            (
                experiment_id,
                prompt_id,
                baseline_version_id,
                challenger_version_id,
                dataset_id,
                json.dumps(success_criteria, ensure_ascii=False),
                now,
            ),
        )
        for key, version_id in (
            ("baseline", baseline_version_id),
            ("challenger", challenger_version_id),
        ):
            await self.database.execute(
                "INSERT INTO prompt_experiment_variants(id, experiment_id, variant_key, "
                "prompt_version_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (_id("variant"), experiment_id, key, version_id, now),
            )
        return await self.get(experiment_id)

    async def get(self, experiment_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_experiments WHERE id = ?", (experiment_id,)
        )
        if row is None:
            raise KeyError(f"实验不存在: {experiment_id}")
        row["success_criteria_data"] = json.loads(row["success_criteria_json"])
        row["comparison_data"] = json.loads(row["comparison_json"])
        return row

    async def list_experiments(
        self,
        *,
        prompt_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if prompt_id:
            rows = await self.database.fetch_all(
                "SELECT * FROM prompt_experiments WHERE prompt_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (prompt_id, max(1, min(limit, 500))),
            )
        else:
            rows = await self.database.fetch_all(
                "SELECT * FROM prompt_experiments ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            )
        for row in rows:
            row["success_criteria_data"] = json.loads(row["success_criteria_json"])
            row["comparison_data"] = json.loads(row["comparison_json"])
        return rows

    async def evaluate(
        self,
        experiment_id: str,
        *,
        executor: PromptExecutor,
        model_provider: str = "evaluation",
        model_name: str = "evaluation-model",
    ) -> dict[str, Any]:
        experiment = await self.get(experiment_id)
        await self.database.execute(
            "UPDATE prompt_experiments SET status = 'RUNNING' WHERE id = ?",
            (experiment_id,),
        )
        baseline_run = await self.evaluator.run(
            prompt_version_id=experiment["baseline_version_id"],
            dataset_id=experiment["dataset_id"],
            model_provider=model_provider,
            model_name=model_name,
            executor=executor,
        )
        challenger_run = await self.evaluator.run(
            prompt_version_id=experiment["challenger_version_id"],
            dataset_id=experiment["dataset_id"],
            model_provider=model_provider,
            model_name=model_name,
            executor=executor,
        )
        await self.database.execute(
            "UPDATE prompt_experiment_variants SET evaluation_run_id = ? "
            "WHERE experiment_id = ? AND variant_key = 'baseline'",
            (baseline_run["id"], experiment_id),
        )
        await self.database.execute(
            "UPDATE prompt_experiment_variants SET evaluation_run_id = ? "
            "WHERE experiment_id = ? AND variant_key = 'challenger'",
            (challenger_run["id"], experiment_id),
        )
        comparison = await self.evaluator.compare(baseline_run["id"], challenger_run["id"])
        recommendation = self._recommend(comparison, experiment["success_criteria_data"])
        await self.database.execute(
            "UPDATE prompt_experiments SET status = 'COMPLETED', recommendation = ?, "
            "comparison_json = ?, completed_at = ? WHERE id = ?",
            (
                recommendation,
                json.dumps(comparison, ensure_ascii=False),
                _now(),
                experiment_id,
            ),
        )
        result = await self.get(experiment_id)
        result["recommendation"] = recommendation
        return result

    @staticmethod
    def _recommend(comparison: dict[str, Any], criteria: dict[str, Any]) -> str:
        baseline = comparison["baseline"]
        challenger = comparison["challenger"]
        if challenger["pass_rate"] < float(criteria.get("minimum_pass_rate", 0.8)):
            return "REJECT"
        if challenger["average_score"] < baseline["average_score"] + float(
            criteria.get("minimum_score_improvement", 0)
        ):
            return "REJECT"
        baseline_cost = float(baseline["total_cost"] or 0)
        challenger_cost = float(challenger["total_cost"] or 0)
        if baseline_cost > 0 and "maximum_cost_increase_percent" in criteria:
            cost_increase = ((challenger_cost - baseline_cost) / baseline_cost) * 100
            if cost_increase > float(criteria["maximum_cost_increase_percent"]):
                return "REJECT"
        baseline_latency = float(baseline["average_latency_ms"] or 0)
        challenger_latency = float(challenger["average_latency_ms"] or 0)
        if baseline_latency > 0 and "maximum_latency_increase_percent" in criteria:
            latency_increase = ((challenger_latency - baseline_latency) / baseline_latency) * 100
            if latency_increase > float(criteria["maximum_latency_increase_percent"]):
                return "REJECT"
        return "CANARY"
