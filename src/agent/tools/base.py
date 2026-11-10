from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any = Field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


RenderHint = Literal["chart", "table", "tree", "cards"]


class ToolRuntimeContext(BaseModel):
    """Stable execution identity passed to canonical tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str | None = None
    task_id: str | None = None
    thread_id: str | None = None
    site_id: str | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ToolProtocol(Protocol):
    name: str
    description: str
    parameters_schema: dict[str, Any]
    render_hint: RenderHint | None

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult: ...


class AgentTool(ABC):
    """Optional convenience base class implementing the canonical protocol."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    render_hint: RenderHint | None = None

    @property
    def parameters(self) -> dict[str, Any]:
        """Compatibility alias for code written before ToolProtocol."""
        return self.parameters_schema

    def resolve_render_hint(self, result_payload: dict[str, Any]) -> RenderHint | None:
        return self.render_hint

    def model_schema(self, *, strict: bool = True) -> dict[str, Any]:
        from src.agent.tools.adapters import to_openai_tool_schema

        return to_openai_tool_schema(self, strict=strict)

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        raise NotImplementedError


def resolve_render_hint(
    tool: ToolProtocol,
    result_payload: dict[str, Any],
) -> RenderHint | None:
    resolver = getattr(tool, "resolve_render_hint", None)
    if callable(resolver):
        return resolver(result_payload)
    return tool.render_hint


def validate_tool_protocol(tool: ToolProtocol) -> None:
    for attribute in ("name", "description"):
        value = getattr(tool, attribute, None)
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f"ToolProtocol.{attribute} 必须是非空字符串")
    schema = getattr(tool, "parameters_schema", None)
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise TypeError("ToolProtocol.parameters_schema 必须是 object JSON Schema")
    execute = getattr(tool, "execute", None)
    if not callable(execute):
        raise TypeError("ToolProtocol.execute 必须可调用")
