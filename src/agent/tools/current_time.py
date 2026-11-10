from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext


class CurrentTimeTool(AgentTool):
    name = "current_time"
    description = "获取指定 IANA 时区的当前日期和时间。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA 时区，例如 Europe/London 或 Asia/Shanghai",
            }
        },
        "required": ["timezone"],
        "additionalProperties": False,
    }

    async def execute(
        self,
        arguments: dict,
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        timezone_name = str(arguments.get("timezone", "")).strip()
        if not timezone_name:
            return ToolResult(success=False, error="timezone 不能为空")
        try:
            current = datetime.now(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            return ToolResult(success=False, error=f"无效时区: {timezone_name}")
        return ToolResult(
            success=True,
            data={
                "timezone": timezone_name,
                "iso_time": current.isoformat(),
                "display_time": current.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "utc_offset": current.strftime("%z"),
            },
        )
