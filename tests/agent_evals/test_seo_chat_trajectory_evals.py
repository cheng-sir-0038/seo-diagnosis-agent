from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.evals.seo_chat import score_run
from src.agent.evals.trajectory import TrajectoryCase, evaluate_agent_run, evaluate_trajectory
from src.agent.loop import AgentLoop
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.tools.base import AgentTool, ToolResult
from src.agent.tools.registry import ToolRegistry

CASES = Path(__file__).with_name("seo_chat_cases.json")


def load_cases() -> list[TrajectoryCase]:
    return [TrajectoryCase.model_validate(item) for item in json.loads(CASES.read_text())]


class SeoTestTool(AgentTool):
    description = "SEO test tool"
    parameters_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, name: str, *, success: bool = True) -> None:
        self.name = name
        self.success = success

    async def execute(self, arguments, runtime=None) -> ToolResult:
        return ToolResult(success=self.success, data={"source": self.name})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chosen_tool", "expected_pass"),
    [("seo_page_metadata", True), ("seo_serp_overview", False)],
)
async def test_evaluates_tools_actually_called_by_agent(chosen_tool, expected_pass) -> None:
    case = next(item for item in load_cases() if item.case_id == "tdk")
    registry = ToolRegistry([
        SeoTestTool("seo_page_metadata"), SeoTestTool("seo_serp_overview"),
        SeoTestTool("seo_page_performance"),
    ])
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(id="call_1", name=chosen_tool, arguments={})],
                    finish_reason="tool_calls")],
        [ModelChunk(text="完成", finish_reason="stop")],
    ])
    run = RunManager().create_run([{"role": "user", "content": case.user_input}])
    await AgentLoop(provider, registry).run(run)

    result = evaluate_agent_run(case, run, available_tools=set(registry.names()))
    assert result.tools == [chosen_tool]
    assert result.passed is expected_pass
    if not expected_pass:
        assert "调用了禁止工具: seo_serp_overview" in result.failures
    else:
        run.partial = True
        partial = evaluate_agent_run(case, run, available_tools=set(registry.names()))
        assert not partial.passed
        assert "运行结果不完整" in partial.failures


@pytest.mark.asyncio
async def test_evaluation_rejects_failed_tool_and_wrong_question() -> None:
    case = next(item for item in load_cases() if item.case_id == "tdk")
    registry = ToolRegistry([SeoTestTool("seo_page_metadata", success=False)])
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(id="call_1", name="seo_page_metadata", arguments={})],
                    finish_reason="tool_calls")],
        [ModelChunk(text="数据获取失败", finish_reason="stop")],
    ])
    run = RunManager().create_run([{"role": "user", "content": "另一个问题"}])
    await AgentLoop(provider, registry).run(run)

    result = evaluate_agent_run(case, run, available_tools=set(registry.names()))
    assert not result.passed
    assert "运行问题与评测用例不一致" in result.failures
    assert "工具执行失败: seo_page_metadata" in result.failures


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


@pytest.mark.parametrize(
    ("answer", "expected", "available"),
    [
        ("Title 是 Example Home，H1 是 Editorial Guide。", "PASS", ["seo_page_metadata"]),
        ("Title 和 H1 看起来都正常。", "FAIL", ["seo_page_metadata"]),
        ("暂时无法读取页面。", "BLOCKED", []),
    ],
)
def test_live_report_checks_evidence_in_the_answer(answer, expected, available) -> None:
    case = next(item for item in load_cases() if item.case_id == "tdk")
    data = {
        "raw": {"data": {"title": "Example Home", "h1s": ["Editorial Guide"]}},
        "provenance": {"source": "page_html"},
    }
    recorded = []
    if available:
        recorded.extend([
            ("tool.started", {"name": "seo_page_metadata"}),
            ("tool.completed", {"name": "seo_page_metadata", "result": {"data": data}}),
        ])
    recorded.extend([
        ("message.completed", {"content": answer}),
        ("run.completed", {"status": "completed"}),
    ])
    run = {
        "id": "run_recorded", "status": "COMPLETED", "model_name": "recorded-model",
        "token_usage": {"total_tokens": 42},
        "events": [
            {"id": f"evt_{i}", "event_type": kind, "sequence": i, "payload": payload,
             "created_at": "2026-01-01T00:00:00Z"}
            for i, (kind, payload) in enumerate(recorded, start=1)
        ],
    }
    task = {"user_intent": case.user_input, "available_tools": available}
    report = score_run(case, run, task, 10)
    assert report["status"] == expected
    assert report["chat_loop_token_usage"] == {"total_tokens": 42}
    if available:
        assert report["evidence"][0]["source"] == "page_html"
        matches = [check["matched"] for check in report["answer_fact_checks"]]
        assert matches == [expected == "PASS"] * 2
