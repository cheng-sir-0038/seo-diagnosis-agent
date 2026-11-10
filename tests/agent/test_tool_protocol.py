from __future__ import annotations

from typing import Any

import pytest

from src.agent.loop import AgentLoop
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.tools import (
    ToolProtocol,
    ToolRegistry,
    ToolResult,
    ToolRuntimeContext,
    to_langchain_tool,
)


class ProtocolOnlyTool:
    name = "protocol_echo"
    description = "Echo input with runtime identity."
    parameters_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    render_hint = None

    def __init__(self) -> None:
        self.runtime: ToolRuntimeContext | None = None

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        self.runtime = runtime
        return ToolResult(
            success=True,
            data={"text": arguments["text"], "run_id": runtime.run_id if runtime else None},
        )


def test_registry_accepts_protocol_without_agenttool_and_emits_openai_schema() -> None:
    tool = ProtocolOnlyTool()
    assert isinstance(tool, ToolProtocol)
    registry = ToolRegistry([tool])

    assert registry.names() == ["protocol_echo"]
    assert registry.get("protocol_echo") is tool
    assert registry.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "protocol_echo",
                "description": "Echo input with runtime identity.",
                "parameters": ProtocolOnlyTool.parameters_schema,
                "strict": True,
            },
        }
    ]
    assert registry.schemas(strict=False)[0]["function"]["strict"] is False
    with pytest.raises(KeyError, match="未知工具"):
        registry.get("missing")
    with pytest.raises(ValueError, match="重复工具"):
        ToolRegistry([tool, ProtocolOnlyTool()])


@pytest.mark.asyncio
async def test_agent_loop_passes_runtime_context_to_protocol_tool() -> None:
    tool = ProtocolOnlyTool()
    manager = RunManager()
    run = manager.create_run(
        [{"role": "user", "content": "echo"}],
        task_id="task_1",
        thread_id="thread_1",
        site_id="site_1",
        conversation_id="conversation_1",
    )
    provider = FakeModelProvider(
        [
            [
                __import__("src.agent.providers.base", fromlist=["ModelChunk"]).ModelChunk(
                    tool_calls=[
                        __import__("src.agent.providers.base", fromlist=["ToolCall"]).ToolCall(
                            id="call_1", name="protocol_echo", arguments={"text": "hello"}
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [
                __import__("src.agent.providers.base", fromlist=["ModelChunk"]).ModelChunk(
                    text="done"
                ),
                __import__("src.agent.providers.base", fromlist=["ModelChunk"]).ModelChunk(
                    finish_reason="stop"
                ),
            ],
        ]
    )

    await AgentLoop(provider, ToolRegistry([tool])).run(run)

    assert tool.runtime is not None
    assert tool.runtime.run_id == run.id
    assert tool.runtime.task_id == "task_1"
    assert tool.runtime.thread_id == "thread_1"
    assert tool.runtime.site_id == "site_1"


@pytest.mark.asyncio
async def test_langchain_adapter_executes_canonical_tool_and_unwraps_data() -> None:
    tool = ProtocolOnlyTool()
    langchain_tool = to_langchain_tool(tool)

    result = await langchain_tool.ainvoke({"text": "SEO"})

    assert result == {"text": "SEO", "run_id": None}
    assert langchain_tool.name == tool.name
    assert langchain_tool.args == tool.parameters_schema["properties"]
