from __future__ import annotations

import uuid
from asyncio import CancelledError
from typing import Any

from sqlalchemy import select, update

from src.observability.storage import ObservabilityDatabase
from src.promptops.evaluation import EvaluationService, PromptExecutor
from src.promptops.registry import PromptRegistry
from src.promptops.schema import ExperimentVariant, PromptExperiment, now


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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
        async with self.database.orm_session(write=True) as session:
            experiment = PromptExperiment(
                id=_id("experiment"),
                prompt_id=prompt_id,
                baseline_version_id=baseline_version_id,
                challenger_version_id=challenger_version_id,
                dataset_id=dataset_id,
                status="CREATED",
                success_criteria_json=success_criteria,
            )
            session.add(experiment)
            await session.flush()
            for key, version_id in (
                ("baseline", baseline_version_id),
                ("challenger", challenger_version_id),
            ):
                session.add(
                    ExperimentVariant(
                        id=_id("variant"),
                        experiment_id=experiment.id,
                        variant_key=key,
                        prompt_version_id=version_id,
                    )
                )
            return experiment.public()

    async def get(self, experiment_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            row = await session.get(PromptExperiment, experiment_id)
            if row is None:
                raise KeyError(f"实验不存在: {experiment_id}")
            return row.public()

    async def list_experiments(
        self,
        *,
        prompt_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = select(PromptExperiment).order_by(PromptExperiment.created_at.desc())
        if prompt_id:
            query = query.where(PromptExperiment.prompt_id == prompt_id)
        async with self.database.orm_session() as session:
            rows = await session.scalars(query.limit(max(1, min(limit, 500))))
            return [row.public() for row in rows]

    async def evaluate(
        self,
        experiment_id: str,
        *,
        executor: PromptExecutor,
        model_provider: str = "evaluation",
        model_name: str = "evaluation-model",
    ) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            row = await session.get(PromptExperiment, experiment_id, with_for_update=True)
            if row is None:
                raise KeyError(f"实验不存在: {experiment_id}")
            if row.status == "RUNNING":
                raise RuntimeError("实验正在执行")
            row.status = "RUNNING"
            row.completed_at = None
            experiment = row.public()

        update_experiment = update(PromptExperiment).where(PromptExperiment.id == experiment_id)
        try:
            runs: dict[str, str] = {}
            for key in ("baseline", "challenger"):
                run = await self.evaluator.run(
                    prompt_version_id=experiment[f"{key}_version_id"],
                    dataset_id=experiment["dataset_id"],
                    model_provider=model_provider,
                    model_name=model_name,
                    executor=executor,
                )
                runs[key] = run["id"]
                await self.database.execute(
                    update(ExperimentVariant)
                    .where(
                        ExperimentVariant.experiment_id == experiment_id,
                        ExperimentVariant.variant_key == key,
                    )
                    .values(evaluation_run_id=run["id"])
                )
            comparison = await self.evaluator.compare(runs["baseline"], runs["challenger"])
            recommendation = self._recommend(comparison, experiment["success_criteria_data"])
            await self.database.execute(
                update_experiment.values(
                    status="COMPLETED",
                    recommendation=recommendation,
                    comparison_json=comparison,
                    completed_at=now(),
                )
            )
        except (Exception, CancelledError):
            await self.database.execute(
                update_experiment.values(status="FAILED", completed_at=now())
            )
            raise
        return await self.get(experiment_id)

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
