from __future__ import annotations

import copy
import json

import pytest

from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.base import ToolResult
from src.agent.tools.registry import ToolRegistry
from src.core.exceptions import (
    InternalAuthError,
    InternalConfigError,
    InternalDataNotAvailableError,
    InternalProviderResponseError,
)


class FailingTool:
    name = "failing_data"
    description = "Exercise the tool error boundary."
    parameters_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    render_hint = None

    def __init__(self, failure):
        self.failure = failure

    async def execute(self, arguments, runtime=None):
        if isinstance(self.failure, Exception):
            raise self.failure
        return self.failure


class RecordingModel(FakeModelProvider):
    def __init__(self):
        super().__init__([
            [ModelChunk(tool_calls=[ToolCall(id="call_error", name="failing_data", arguments={})],
                        finish_reason="tool_calls")],
            [ModelChunk(text="已说明本次数据限制。"), ModelChunk(finish_reason="stop")],
        ])
        self.inputs = []

    async def stream(self, messages, tools, *, tool_choice="auto"):
        self.inputs.append(copy.deepcopy(messages))
        async for chunk in super().stream(messages, tools, tool_choice=tool_choice):
            yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code", [
    (InternalAuthError("数据源拒绝该 API 账号", debug_context={"api_key": "private-debug"}),
     "AUTH_FAILED"),
    (InternalDataNotAvailableError("本次查询没有可用数据"), "DATA_NOT_AVAILABLE"),
    (InternalConfigError("数据源配置缺失"), "CONFIG_ERROR"),
    (InternalProviderResponseError("数据源响应格式异常"), "UPSTREAM_ERROR"),
    (RuntimeError("private-internal-detail"), "INTERNAL_ERROR"),
    (TimeoutError(), "TOOL_TIMEOUT"),
])
async def test_error_code_reaches_model_and_matches_runtime_event(failure, code):
    model = RecordingModel()
    run = RunManager().create_run([{"role": "user", "content": "检查数据"}])
    await AgentExecutor(model, ToolRegistry([FailingTool(failure)]), max_iterations=3).run(run)
    message = next(item for item in model.inputs[1] if item["role"] == "tool")
    payload = json.loads(message["content"])
    assert payload["success"] is False
    assert payload["error_code"] == code
    assert "private-debug" not in message["content"]
    assert "private-internal-detail" not in message["content"]
    event = next(event for event in run.events if event.type == "tool.failed")
    assert event.payload["error_code"] == payload["error_code"]


@pytest.mark.asyncio
async def test_explicit_tool_failure_keeps_its_error_code():
    model = RecordingModel()
    run = RunManager().create_run([{"role": "user", "content": "检查数据"}])
    result = ToolResult(success=False, error="本次没有数据", error_code="DATA_NOT_AVAILABLE")
    await AgentExecutor(model, ToolRegistry([FailingTool(result)]), max_iterations=3).run(run)
    event = next(event for event in run.events if event.type == "tool.failed")
    assert event.payload["result"]["error_code"] == "DATA_NOT_AVAILABLE"
    assert event.payload["error_code"] == "DATA_NOT_AVAILABLE"
