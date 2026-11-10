from __future__ import annotations

from collections.abc import Iterable

from src.agent.tools.adapters import to_openai_tool_schema
from src.agent.tools.base import ToolProtocol, validate_tool_protocol
from src.agent.tools.calculator import CalculatorTool
from src.agent.tools.current_time import CurrentTimeTool


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolProtocol] | None = None) -> None:
        self._tools: dict[str, ToolProtocol] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolProtocol) -> None:
        validate_tool_protocol(tool)
        if tool.name in self._tools:
            raise ValueError(f"重复工具注册: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self, *, strict: bool = True) -> list[dict]:
        return [to_openai_tool_schema(tool, strict=strict) for tool in self._tools.values()]

    def get(self, name: str) -> ToolProtocol:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"未知工具: {name}") from exc

    def names(self) -> list[str]:
        return list(self._tools)

    def values(self) -> list[ToolProtocol]:
        return list(self._tools.values())


def build_default_registry() -> ToolRegistry:
    return ToolRegistry([CurrentTimeTool(), CalculatorTool()])
