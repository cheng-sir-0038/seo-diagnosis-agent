from __future__ import annotations

import pytest

from src.agent.loop import AgentLoop
from src.agent.models import RunStatus
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.tools.registry import build_default_registry


@pytest.mark.asyncio
async def test_agent_loop_streams_text_and_completes() -> None:
    manager = RunManager()
    run = manager.create_run([{"role": "user", "content": "你好"}])
    provider = FakeModelProvider.text("你好，我是测试 Agent。")
    await AgentLoop(provider, build_default_registry()).run(run)
    types = [event.type for event in run.events]
    assert "message.delta" in types
    assert types[-1] == "run.completed"
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_agent_loop_executes_tool_then_continues() -> None:
    manager = RunManager()
    run = manager.create_run([{"role": "user", "content": "1234 * 56"}])
    provider = FakeModelProvider.calculator_then_text("1234 * 56", "结果是 69104。")
    await AgentLoop(provider, build_default_registry()).run(run)
    types = [event.type for event in run.events]
    assert "tool.started" in types
    assert "tool.completed" in types
    assert types[-1] == "run.completed"


@pytest.mark.asyncio
async def test_agent_loop_failure_sets_finished_at() -> None:
    manager = RunManager()
    run = manager.create_run([{"role": "user", "content": "test"}])
    provider = FakeModelProvider.text("unused")

    await AgentLoop(provider, build_default_registry(), max_iterations=0).run(run)

    assert run.status == RunStatus.FAILED
    assert run.finished_at is not None


def test_run_manager_cleans_expired_terminal_runs() -> None:
    from datetime import UTC, datetime, timedelta

    manager = RunManager(retention_seconds=60)
    run = manager.create_run([{"role": "user", "content": "test"}])
    run.status = RunStatus.COMPLETED
    run.finished_at = datetime.now(UTC) - timedelta(seconds=61)

    assert manager.cleanup_finished() == 1
    assert manager.get(run.id) is None


class CountingEchoTool:
    name = "counting_echo"
    description = "Return the supplied value and count real executions."
    parameters_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    render_hint = None

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments, runtime=None):
        from src.agent.tools.base import ToolResult

        self.calls += 1
        return ToolResult(success=True, data={"value": arguments["value"]})


@pytest.mark.asyncio
async def test_agent_loop_reuses_identical_successful_tool_call() -> None:
    from src.agent.providers.base import ModelChunk, ToolCall
    from src.agent.tools.registry import ToolRegistry

    tool = CountingEchoTool()
    provider = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[ToolCall(id="call_1", name=tool.name, arguments={"value": "same"})],
                    finish_reason="tool_calls",
                )
            ],
            [
                ModelChunk(
                    tool_calls=[ToolCall(id="call_2", name=tool.name, arguments={"value": "same"})],
                    finish_reason="tool_calls",
                )
            ],
            [ModelChunk(text="已使用现有证据完成。"), ModelChunk(finish_reason="stop")],
        ]
    )
    run = RunManager().create_run([{"role": "user", "content": "重复工具测试"}])

    await AgentLoop(provider, ToolRegistry([tool]), max_iterations=4).run(run)

    assert run.status == RunStatus.COMPLETED
    assert tool.calls == 1
    completed = [event for event in run.events if event.type == "tool.completed"]
    assert len(completed) == 2
    assert completed[1].payload["reused"] is True
    assert completed[1].payload["reused_from_tool_call_id"] == "call_1"
    assert provider.tool_choices == ["auto", "auto", "none"]
    final = next(event for event in reversed(run.events) if event.type == "run.completed")
    assert final.payload["completion_mode"] == "duplicate_tool_guard"


@pytest.mark.asyncio
async def test_agent_loop_reserves_last_iteration_for_forced_summary() -> None:
    from src.agent.providers.base import ModelChunk, ToolCall
    from src.agent.tools.registry import ToolRegistry

    tool = CountingEchoTool()
    provider = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[
                        ToolCall(id="call_1", name=tool.name, arguments={"value": "evidence"})
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [ModelChunk(text="证据已经足够。"), ModelChunk(finish_reason="stop")],
        ]
    )
    run = RunManager().create_run([{"role": "user", "content": "两轮完成"}])

    await AgentLoop(provider, ToolRegistry([tool]), max_iterations=2).run(run)

    assert run.status == RunStatus.COMPLETED
    assert provider.tool_choices == ["auto", "none"]
    completed = next(event for event in reversed(run.events) if event.type == "run.completed")
    assert completed.payload["partial"] is True
    assert completed.payload["completion_mode"] == "budget_limit"
    message = next(event for event in reversed(run.events) if event.type == "message.completed")
    assert "达到本轮调查预算" in message.payload["content"]


@pytest.mark.asyncio
async def test_agent_loop_recovers_exhausted_run_by_summarizing_without_tools() -> None:
    from src.agent.providers.base import ModelChunk
    from src.agent.tools.registry import ToolRegistry

    tool = CountingEchoTool()
    provider = FakeModelProvider(
        [[ModelChunk(text="基于已有 TDK 和关键词证据完成总结。"), ModelChunk(finish_reason="stop")]]
    )
    run = RunManager().create_run(
        [
            {"role": "user", "content": "检查 TDK 和关键词趋势"},
            {
                "role": "tool",
                "tool_call_id": "old_call",
                "name": tool.name,
                "content": '{"success":true,"data":{"value":"old"}}',
            },
        ]
    )
    run.status = RunStatus.RECOVERING
    run.iteration = 8
    run.tool_count = 11
    run.error = "超过最大模型循环次数"

    await AgentLoop(provider, ToolRegistry([tool]), max_iterations=8).run(run)

    assert run.status == RunStatus.COMPLETED
    assert run.error is None
    assert tool.calls == 0
    assert provider.calls == 1
    assert provider.tool_choices == ["none"]
    completed = next(event for event in reversed(run.events) if event.type == "run.completed")
    assert completed.payload["partial"] is True


@pytest.mark.asyncio
async def test_budget_summary_hides_textual_dsml_tool_markup() -> None:
    from src.agent.providers.base import ModelChunk
    from src.agent.tools.registry import ToolRegistry

    provider = FakeModelProvider(
        [
            [
                ModelChunk(text="好的，我继续查询。\n\n"),
                ModelChunk(
                    text=(
                        "<｜｜DSML｜｜tool_calls>"
                        '<｜｜DSML｜｜invoke name="seo_keyword_overview">'
                        "</｜｜DSML｜｜invoke>"
                        "</｜｜DSML｜｜tool_calls>"
                    )
                ),
                ModelChunk(finish_reason="stop"),
            ]
        ]
    )
    run = RunManager().create_run([{"role": "user", "content": "寻找关键词"}])
    run.status = RunStatus.RECOVERING
    run.iteration = 8
    run.survival_summary = {
        "confirmed_facts": ["关键词明细：50 条记录"],
        "limitations": ["缺少长期历史趋势"],
    }

    await AgentLoop(provider, ToolRegistry([]), max_iterations=8).run(run)

    assert run.status == RunStatus.COMPLETED
    completed = next(event for event in run.events if event.type == "message.completed")
    assert "DSML" not in completed.payload["content"]
    assert "seo_keyword_overview" not in completed.payload["content"]
    assert "关键词明细：50 条记录" in completed.payload["content"]
    assert not any(
        event.type == "message.delta" and "DSML" in str(event.payload.get("delta"))
        for event in run.events
    )
