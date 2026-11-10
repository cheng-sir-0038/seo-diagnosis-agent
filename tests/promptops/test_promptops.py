from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.promptops.evaluation import EvaluationService, ExecutionResult
from src.promptops.experiments import ExperimentService
from src.promptops.models import CandidatePrompt, VersionStage
from src.promptops.optimization import PromptOptimizationLoop
from src.promptops.registry import PromptRegistry


class FakeExecutor:
    async def execute(self, version: dict[str, Any], case: dict[str, Any]) -> ExecutionResult:
        good = "strict" in version["system_template"]
        output = {"answer": case["input_json_data"]["question"]}
        if good:
            output["evidence"] = ["source-1"]
        return ExecutionResult(
            output=output,
            input_tokens=40,
            output_tokens=20,
            estimated_cost=0.002 if good else 0.001,
            latency_ms=120 if good else 80,
        )


class FakeCandidateGenerator:
    async def generate(self, *, current_version: dict, failures: list[dict]) -> CandidatePrompt:
        assert failures
        return CandidatePrompt(
            system_template=current_version["system_template"] + "\nstrict evidence required",
            user_template=current_version["user_template"],
            change_summary="要求 evidence 字段并禁止无依据结论",
        )


@pytest.mark.asyncio
async def test_prompt_versions_evaluation_promotion_and_rollback(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "promptops.db")
    await database.startup()
    registry = PromptRegistry(database)
    await registry.startup()
    try:
        prompt = await registry.create_prompt(
            prompt_key="seo.opportunity.classify",
            name="机会分类",
            purpose="从证据生成机会",
        )
        v1 = await registry.create_version(
            prompt_id=prompt["id"],
            system_template="classify opportunity",
            user_template="{{input}}",
            output_schema={"required": ["answer", "evidence"]},
            stage=VersionStage.DRAFT,
        )
        v2 = await registry.create_version(
            prompt_id=prompt["id"],
            system_template="classify opportunity with strict evidence",
            user_template="{{input}}",
            output_schema={"required": ["answer", "evidence"]},
            stage=VersionStage.CHALLENGER,
            parent_version_id=v1["id"],
        )
        await registry.promote(prompt["id"], v1["id"], environment="production")
        dataset = await registry.create_dataset(
            name="SEO opportunity regression", prompt_id=prompt["id"]
        )
        await registry.add_case(
            dataset_id=dataset["id"],
            name="must cite evidence",
            input_data={"question": "maintenance cost"},
            expected_output={"answer": "maintenance cost"},
            grading_rules={
                "required_paths": ["answer", "evidence"],
                "exact_match_fields": ["answer"],
                "forbidden_terms": ["guaranteed ranking"],
                "pass_threshold": 0.8,
            },
        )
        evaluator = EvaluationService(database)
        baseline = await evaluator.run(
            prompt_version_id=v1["id"],
            dataset_id=dataset["id"],
            model_provider="fake",
            model_name="fake-model",
            executor=FakeExecutor(),
        )
        challenger = await evaluator.run(
            prompt_version_id=v2["id"],
            dataset_id=dataset["id"],
            model_provider="fake",
            model_name="fake-model",
            executor=FakeExecutor(),
        )
        comparison = await evaluator.compare(baseline["id"], challenger["id"])
        assert comparison["challenger"]["pass_rate"] == 1.0
        assert comparison["baseline"]["pass_rate"] == 0.0

        experiments = ExperimentService(database, registry, evaluator)
        experiment = await experiments.create(
            prompt_id=prompt["id"],
            baseline_version_id=v1["id"],
            challenger_version_id=v2["id"],
            dataset_id=dataset["id"],
            success_criteria={
                "minimum_pass_rate": 1.0,
                "maximum_cost_increase_percent": 150,
                "maximum_latency_increase_percent": 100,
            },
        )
        result = await experiments.evaluate(experiment["id"], executor=FakeExecutor())
        assert result["recommendation"] == "CANARY"
        deployment = await registry.start_canary(
            prompt["id"], v2["id"], environment="production", percentage=10
        )
        assert deployment["current_version_id"] == v1["id"]
        assert deployment["canary_version_id"] == v2["id"]
        promoted = await registry.promote(prompt["id"], v2["id"], environment="production")
        assert promoted["current_version_id"] == v2["id"]
        rolled_back = await registry.rollback(
            prompt["id"], v1["id"], environment="production", reason="quality regression"
        )
        assert rolled_back["current_version_id"] == v1["id"]
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_optimization_loop_creates_challenger_and_never_auto_promotes(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "promptops.db")
    await database.startup()
    registry = PromptRegistry(database)
    await registry.startup()
    try:
        prompt = await registry.create_prompt(
            prompt_key="seo.prd.generate", name="PRD", purpose="PRD"
        )
        current = await registry.create_version(
            prompt_id=prompt["id"],
            system_template="generate prd",
            user_template="{{input}}",
            output_schema={"required": ["answer", "evidence"]},
        )
        await registry.promote(prompt["id"], current["id"], environment="production")
        dataset = await registry.create_dataset(name="prd failures", prompt_id=prompt["id"])
        await registry.add_case(
            dataset_id=dataset["id"],
            name="evidence",
            input_data={"question": "pricing"},
            expected_output={"answer": "pricing"},
            grading_rules={"required_paths": ["answer", "evidence"], "pass_threshold": 0.8},
        )
        evaluator = EvaluationService(database)
        loop = PromptOptimizationLoop(
            database=database,
            registry=registry,
            evaluator=evaluator,
            experiments=ExperimentService(database, registry, evaluator),
            candidate_generator=FakeCandidateGenerator(),
        )
        result = await loop.run(
            prompt_id=prompt["id"],
            dataset_id=dataset["id"],
            environment="production",
            failures=[{"case": "missing evidence"}],
            executor=FakeExecutor(),
            success_criteria={"minimum_pass_rate": 1.0},
        )
        deployment = await registry.get_deployment(prompt["id"], "production")
        challenger = await registry.get_version(result["challenger_version_id"])
        assert result["recommendation"] == "CANARY"
        assert challenger["stage"] == "CANARY"
        assert deployment["current_version_id"] == current["id"]
        assert deployment["canary_version_id"] == challenger["id"]
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_model_call_can_link_exact_prompt_version(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "promptops.db")
    await database.startup()
    registry = PromptRegistry(database)
    await registry.startup()
    client = ObservabilityClient(database, project_key="test")
    try:
        prompt = await registry.create_prompt(prompt_key="test.prompt", name="Test", purpose="Test")
        version = await registry.create_version(
            prompt_id=prompt["id"],
            system_template="system",
            user_template="user",
            output_schema={},
        )
        async with client.trace("prompt.call") as trace:
            async with trace.run(trigger_type="test") as run:
                async with run.span("model") as span:
                    call = await span.model_call(
                        provider="fake",
                        model="fake",
                        operation="invoke",
                        prompt_key=prompt["prompt_key"],
                        prompt_version=version["version_label"],
                        prompt_version_id=version["id"],
                    )
        assert call["prompt_version_id"] == version["id"]
    finally:
        await database.shutdown()
