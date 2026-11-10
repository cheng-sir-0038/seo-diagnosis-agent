from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.evals.trajectory import TrajectoryCase, evaluate_trajectory

CASES = Path(__file__).with_name("seo_chat_cases.json")


def load_cases() -> list[TrajectoryCase]:
    return [TrajectoryCase.model_validate(item) for item in json.loads(CASES.read_text())]


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case.case_id)
def test_expected_seo_chat_trajectories_pass(case: TrajectoryCase) -> None:
    result = evaluate_trajectory(
        case,
        tools=case.ordered_tools,
        terminal_status=case.terminal_status,
        available_tools=set(case.required_tools) | {"seo_search_metrics"},
    )
    assert result.passed is True
    assert result.failures == []


def test_tdk_eval_rejects_serp_substitution() -> None:
    case = next(item for item in load_cases() if item.case_id == "tdk")
    result = evaluate_trajectory(
        case,
        tools=["seo_serp_overview"],
        terminal_status="completed",
        available_tools={"seo_page_metadata", "seo_serp_overview"},
    )
    assert result.passed is False
    assert "缺少必需工具: seo_page_metadata" in result.failures
    assert "调用了禁止工具: seo_serp_overview" in result.failures


def test_eval_rejects_pseudo_tool_exposure() -> None:
    case = next(item for item in load_cases() if item.case_id == "organic-keywords")
    result = evaluate_trajectory(
        case,
        tools=["seo_organic_keywords"],
        terminal_status="completed",
        available_tools={"seo_organic_keywords", "seo_search_history"},
    )
    assert result.passed is False
    assert "禁止工具出现在可用工具列表: seo_search_history" in result.failures
