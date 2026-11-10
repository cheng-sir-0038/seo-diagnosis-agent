from __future__ import annotations

from copy import deepcopy
from typing import Any

from langchain_core.tools import StructuredTool

from src.agent.tools.base import ToolProtocol, ToolRuntimeContext


def to_openai_tool_schema(
    tool: ToolProtocol,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    parameters = deepcopy(tool.parameters_schema)
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    if strict:
        parameters.setdefault("additionalProperties", False)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
            "strict": strict,
        },
    }


def to_langchain_tool(tool: ToolProtocol) -> StructuredTool:
    """Adapt a canonical tool for LangChain/LangGraph ToolNode consumers."""

    async def invoke_tool(**arguments: Any) -> Any:
        result = await tool.execute(arguments, runtime=None)
        if not result.success:
            raise RuntimeError(result.error or f"工具执行失败: {tool.name}")
        return result.data

    return StructuredTool.from_function(
        coroutine=invoke_tool,
        name=tool.name,
        description=tool.description,
        args_schema=deepcopy(tool.parameters_schema),
    )


def runtime_context_from_values(
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    thread_id: str | None = None,
    site_id: str | None = None,
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id=run_id,
        task_id=task_id,
        thread_id=thread_id,
        site_id=site_id,
        conversation_id=conversation_id,
        metadata=metadata or {},
    )
