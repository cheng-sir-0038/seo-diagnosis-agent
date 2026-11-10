from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from src.observability.storage import ObservabilityDatabase
from src.promptops.evaluation import EvaluationService, PromptExecutor
from src.promptops.experiments import ExperimentService
from src.promptops.models import CandidatePrompt, VersionStage
from src.promptops.registry import PromptRegistry


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CandidateGenerator(Protocol):
    async def generate(
        self, *, current_version: dict[str, Any], failures: list[dict[str, Any]]
    ) -> CandidatePrompt: ...


class PromptOptimizationLoop:
    def __init__(
        self,
        *,
        database: ObservabilityDatabase,
        registry: PromptRegistry,
        evaluator: EvaluationService,
        experiments: ExperimentService,
        candidate_generator: CandidateGenerator,
    ) -> None:
        self.database = database
        self.registry = registry
        self.evaluator = evaluator
        self.experiments = experiments
        self.candidate_generator = candidate_generator

    async def run(
        self,
        *,
        prompt_id: str,
        dataset_id: str,
        environment: str,
        failures: list[dict[str, Any]],
        executor: PromptExecutor,
        success_criteria: dict[str, Any],
        canary_percentage: int = 10,
    ) -> dict[str, Any]:
        deployment = await self.registry.get_deployment(prompt_id, environment)
        current_id = deployment.get("current_version_id")
        if not current_id:
            raise RuntimeError("Prompt 没有 CURRENT 版本")
        current = await self.registry.get_version(str(current_id))
        optimization_id = _id("optimization")
        await self.database.execute(
            "INSERT INTO prompt_optimization_runs(id, prompt_id, current_version_id, dataset_id, "
            "status, failures_json, created_at) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)",
            (
                optimization_id,
                prompt_id,
                current["id"],
                dataset_id,
                json.dumps(failures, ensure_ascii=False),
                _now(),
            ),
        )
        candidate = await self.candidate_generator.generate(
            current_version=current,
            failures=failures,
        )
        challenger = await self.registry.create_version(
            prompt_id=prompt_id,
            system_template=candidate.system_template,
            user_template=candidate.user_template,
            output_schema=candidate.output_schema or current["output_schema_data"],
            config=candidate.config or current["config_data"],
            stage=VersionStage.CHALLENGER,
            parent_version_id=current["id"],
            change_summary=candidate.change_summary,
            created_by="prompt-optimization-loop",
        )
        experiment = await self.experiments.create(
            prompt_id=prompt_id,
            baseline_version_id=current["id"],
            challenger_version_id=challenger["id"],
            dataset_id=dataset_id,
            success_criteria=success_criteria,
        )
        result = await self.experiments.evaluate(experiment["id"], executor=executor)
        if result["recommendation"] == "CANARY":
            await self.registry.start_canary(
                prompt_id,
                challenger["id"],
                environment=environment,
                percentage=canary_percentage,
            )
        await self.database.execute(
            "UPDATE prompt_optimization_runs SET challenger_version_id = ?, experiment_id = ?, "
            "status = 'COMPLETED', recommendation = ?, completed_at = ? WHERE id = ?",
            (
                challenger["id"],
                experiment["id"],
                result["recommendation"],
                _now(),
                optimization_id,
            ),
        )
        return {
            "optimization_run_id": optimization_id,
            "challenger_version_id": challenger["id"],
            "experiment_id": experiment["id"],
            "recommendation": result["recommendation"],
        }
