from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from src.observability.storage import ObservabilityDatabase
from src.promptops.models import EvaluationScore
from src.promptops.registry import PromptRegistry


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _get_path(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


class ExecutionResult(BaseModel):
    output: Any = Field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    latency_ms: float | None = None
    error_message: str | None = None


class PromptExecutor(Protocol):
    async def execute(self, version: dict[str, Any], case: dict[str, Any]) -> ExecutionResult: ...


class EvaluationService:
    def __init__(self, database: ObservabilityDatabase) -> None:
        self.database = database
        self.registry = PromptRegistry(database)

    async def run(
        self,
        *,
        prompt_version_id: str,
        dataset_id: str,
        model_provider: str,
        model_name: str,
        executor: PromptExecutor,
    ) -> dict[str, Any]:
        version = await self.registry.get_version(prompt_version_id)
        cases = await self.registry.list_cases(dataset_id)
        run_id = _id("eval")
        started_at = _now()
        await self.database.execute(
            "INSERT INTO prompt_evaluation_runs(id, prompt_version_id, dataset_id, model_provider, "
            "model_name, status, case_count, started_at) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?)",
            (
                run_id,
                prompt_version_id,
                dataset_id,
                model_provider,
                model_name,
                len(cases),
                started_at,
            ),
        )
        passed_count = 0
        scores: list[float] = []
        costs: list[float] = []
        latencies: list[float] = []
        try:
            for case in cases:
                result = await executor.execute(version, case)
                score = self.grade(version, case, result)
                if score.passed:
                    passed_count += 1
                scores.append(score.total_score)
                if result.estimated_cost is not None:
                    costs.append(result.estimated_cost)
                if result.latency_ms is not None:
                    latencies.append(result.latency_ms)
                await self.database.execute(
                    "INSERT INTO prompt_evaluation_results(id, evaluation_run_id, case_id, "
                    "status, output_json, scores_json, total_score, passed, input_tokens, "
                    "output_tokens, estimated_cost, latency_ms, error_message, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _id("evalresult"),
                        run_id,
                        case["id"],
                        "COMPLETED" if not result.error_message else "FAILED",
                        json.dumps(result.output, ensure_ascii=False),
                        score.model_dump_json(),
                        score.total_score,
                        int(score.passed),
                        result.input_tokens,
                        result.output_tokens,
                        result.estimated_cost,
                        result.latency_ms,
                        result.error_message,
                        _now(),
                    ),
                )
            case_count = len(cases)
            await self.database.execute(
                "UPDATE prompt_evaluation_runs SET status = 'COMPLETED', passed_count = ?, "
                "pass_rate = ?, average_score = ?, total_cost = ?, average_latency_ms = ?, "
                "completed_at = ? WHERE id = ?",
                (
                    passed_count,
                    (passed_count / case_count) if case_count else 0,
                    (sum(scores) / len(scores)) if scores else 0,
                    sum(costs),
                    (sum(latencies) / len(latencies)) if latencies else 0,
                    _now(),
                    run_id,
                ),
            )
        except Exception as exc:
            await self.database.execute(
                "UPDATE prompt_evaluation_runs SET status = 'FAILED', error_message = ?, "
                "completed_at = ? WHERE id = ?",
                (str(exc)[:2000], _now(), run_id),
            )
            raise
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_evaluation_runs WHERE id = ?", (run_id,)
        )
        assert row is not None
        return row

    async def list_runs(
        self,
        *,
        prompt_version_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if prompt_version_id:
            return await self.database.fetch_all(
                "SELECT * FROM prompt_evaluation_runs WHERE prompt_version_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (prompt_version_id, max(1, min(limit, 500))),
            )
        return await self.database.fetch_all(
            "SELECT * FROM prompt_evaluation_runs ORDER BY started_at DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        )

    async def get_run(self, run_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_evaluation_runs WHERE id = ?", (run_id,)
        )
        if row is None:
            raise KeyError(f"评测运行不存在: {run_id}")
        row["results"] = await self.database.fetch_all(
            "SELECT r.*, c.name AS case_name FROM prompt_evaluation_results r "
            "JOIN prompt_dataset_cases c ON c.id = r.case_id "
            "WHERE r.evaluation_run_id = ? ORDER BY c.created_at",
            (run_id,),
        )
        return row

    def grade(
        self,
        version: dict[str, Any],
        case: dict[str, Any],
        result: ExecutionResult,
    ) -> EvaluationScore:
        output = result.output
        rules = case["grading_rules_data"]
        expected = case["expected_output_data"]
        required_paths = list(rules.get("required_paths") or [])
        schema_required = list(version.get("output_schema_data", {}).get("required") or [])
        for path in schema_required:
            if path not in required_paths:
                required_paths.append(path)
        required_results = [_get_path(output, path)[0] for path in required_paths]
        completeness = (
            sum(int(value) for value in required_results) / len(required_results)
            if required_results
            else 1.0
        )
        exact_fields = list(rules.get("exact_match_fields") or [])
        exact_results: list[bool] = []
        for path in exact_fields:
            actual_found, actual = _get_path(output, path)
            expected_found, expected_value = _get_path(expected, path)
            exact_results.append(actual_found and expected_found and actual == expected_value)
        exact_match = (
            sum(int(value) for value in exact_results) / len(exact_results)
            if exact_results
            else 1.0
        )
        serialized = json.dumps(output, ensure_ascii=False).lower()
        forbidden_terms = [str(term).lower() for term in rules.get("forbidden_terms") or []]
        forbidden_hits = [term for term in forbidden_terms if term in serialized]
        forbidden_safety = 0.0 if forbidden_hits else 1.0
        format_validity = 1.0 if isinstance(output, dict) and not result.error_message else 0.0
        total = round(
            (format_validity + completeness + exact_match + forbidden_safety) / 4,
            6,
        )
        threshold = float(rules.get("pass_threshold", 0.8))
        passed = (
            total >= threshold
            and all(required_results)
            and all(exact_results or [True])
            and not forbidden_hits
            and not result.error_message
        )
        return EvaluationScore(
            format_validity=format_validity,
            completeness=completeness,
            exact_match=exact_match,
            forbidden_term_safety=forbidden_safety,
            total_score=total,
            passed=passed,
            details={
                "missing_paths": [
                    path
                    for path, present in zip(required_paths, required_results, strict=False)
                    if not present
                ],
                "forbidden_hits": forbidden_hits,
                "error_message": result.error_message,
            },
        )

    async def compare(self, baseline_run_id: str, challenger_run_id: str) -> dict[str, Any]:
        baseline = await self._summary(baseline_run_id)
        challenger = await self._summary(challenger_run_id)
        return {
            "baseline": baseline,
            "challenger": challenger,
            "delta": {
                "pass_rate": challenger["pass_rate"] - baseline["pass_rate"],
                "average_score": challenger["average_score"] - baseline["average_score"],
                "total_cost": challenger["total_cost"] - baseline["total_cost"],
                "average_latency_ms": (
                    challenger["average_latency_ms"] - baseline["average_latency_ms"]
                ),
            },
        }

    async def _summary(self, run_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_evaluation_runs WHERE id = ?", (run_id,)
        )
        if row is None:
            raise KeyError(f"评测运行不存在: {run_id}")
        return row
