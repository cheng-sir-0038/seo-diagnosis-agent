from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class SurvivalSummary(BaseModel):
    objective: str = ""
    completed_tools: list[dict[str, str]] = Field(default_factory=list)
    confirmed_facts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    pending: str = ""
    next_action: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)
    human_input: dict[str, Any] | None = None

    def as_prompt(self) -> str:
        lines = [
            "Survival Summary（运行恢复摘要）",
            f"目标：{self.objective or '未识别'}",
            f"当前阶段：{self.pending or 'unknown'}",
            f"下一步：{self.next_action or '继续根据当前证据判断'}",
        ]
        if self.completed_tools:
            lines.append(
                "已完成工具："
                + "；".join(
                    f"{item.get('name')}({item.get('tool_call_id')})"
                    for item in self.completed_tools
                )
            )
        if self.confirmed_facts:
            lines.append("已确认事实：" + "；".join(self.confirmed_facts[:12]))
        if self.limitations:
            lines.append("数据限制：" + "；".join(self.limitations[:12]))
        return "\n".join(lines)


def build_survival_summary(
    *,
    messages: list[dict[str, Any]],
    events: list[Any],
    stage: str,
    token_usage: dict[str, int] | None = None,
) -> SurvivalSummary:
    objective = ""
    for message in reversed(messages):
        if message.get("role") == "user" and message.get("content"):
            objective = str(message["content"]).strip()
            break

    completed_tools: list[dict[str, str]] = []
    confirmed_facts: list[str] = []
    limitations: list[str] = []
    for raw in events:
        event_type, payload = _event_parts(raw)
        if event_type == "tool.completed":
            name = str(payload.get("name") or "unknown")
            call_id = str(payload.get("tool_call_id") or "")
            marker = {"name": name, "tool_call_id": call_id}
            if marker not in completed_tools:
                completed_tools.append(marker)
            result = payload.get("result")
            if isinstance(result, dict):
                data = result.get("data") if isinstance(result.get("data"), dict) else result
                fact = _fact_from_data(name, data)
                if fact and fact not in confirmed_facts:
                    confirmed_facts.append(fact)
                for item in _limitations_from_data(data):
                    if item not in limitations:
                        limitations.append(item)
        elif event_type == "tool.failed":
            name = str(payload.get("name") or "unknown")
            error = payload.get("result") or payload.get("error_code") or "工具失败"
            text = f"{name}：{_short_text(error)}"
            if text not in limitations:
                limitations.append(text)

    next_action = _next_action(stage)
    return SurvivalSummary(
        objective=objective,
        completed_tools=completed_tools,
        confirmed_facts=confirmed_facts,
        limitations=limitations,
        pending=stage,
        next_action=next_action,
        token_usage={key: int(value or 0) for key, value in (token_usage or {}).items()},
    )


def compact_model_context(
    messages: list[dict[str, Any]],
    summary: SurvivalSummary | dict[str, Any],
    *,
    message_limit: int = 24,
    character_limit: int = 50_000,
) -> list[dict[str, Any]]:
    if len(messages) <= message_limit and _message_characters(messages) <= character_limit:
        return [dict(message) for message in messages]
    model = (
        summary if isinstance(summary, SurvivalSummary) else SurvivalSummary.model_validate(summary)
    )
    system = next((dict(item) for item in messages if item.get("role") == "system"), None)
    latest_user = max(
        (index for index, item in enumerate(messages) if item.get("role") == "user"),
        default=max(0, len(messages) - message_limit + 2),
    )
    current_turn = [dict(item) for item in messages[latest_user:]]
    compacted: list[dict[str, Any]] = []
    if system is not None:
        compacted.append(system)
    compacted.append({"role": "system", "content": model.as_prompt()})
    for message in current_turn:
        next_message = dict(message)
        if next_message.get("role") == "tool":
            next_message["content"] = json.dumps(
                {
                    "compacted": True,
                    "message": (
                        "完整工具结果已保存在持久化 Checkpoint，"
                        "请依据 Survival Summary 中的已确认事实继续。"
                    ),
                },
                ensure_ascii=False,
            )
        compacted.append(next_message)
    return _fit_character_budget(compacted, character_limit)


def _event_parts(raw: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(raw, dict):
        event_type = str(raw.get("type") or raw.get("event_type") or "")
        payload = raw.get("payload") or {}
    else:
        event_type = str(getattr(raw, "type", ""))
        payload = getattr(raw, "payload", {})
    return event_type, payload if isinstance(payload, dict) else {}


def _fact_from_data(name: str, data: Any) -> str:
    if not isinstance(data, dict):
        return f"{name} 已返回结果"
    title = data.get("title")
    if isinstance(title, dict):
        title_text = str(title.get("fallback") or title.get("key") or name)
    else:
        title_text = str(title or name)
    source = data.get("provenance")
    source_text = ""
    if isinstance(source, dict):
        source_text = str(source.get("source_label") or source.get("source") or "")
    details: list[str] = []
    rows = data.get("rows")
    if isinstance(rows, list):
        details.append(f"{len(rows)} 条记录")
    metrics = data.get("metrics")
    if isinstance(metrics, list):
        for item in metrics[:4]:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or item.get("key")
            value = item.get("display_value", item.get("value"))
            if label is not None and value is not None:
                details.append(f"{label}={value}")
    checks = data.get("checks")
    if isinstance(checks, list):
        for item in checks[:4]:
            if isinstance(item, dict) and item.get("value"):
                details.append(f"{item.get('label') or item.get('key')}={item['value']}")
    suffix = "，".join(details)
    source_prefix = f"{source_text} " if source_text else ""
    return f"{source_prefix}{title_text}" + (f"：{suffix}" if suffix else "")


def _limitations_from_data(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    values = data.get("limitations") or []
    return [str(item) for item in values if str(item).strip()] if isinstance(values, list) else []


def _short_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text[:300]


def _next_action(stage: str) -> str:
    return {
        "calling_model": "让模型根据现有证据决定下一步",
        "tools_pending": "执行尚未完成的工具调用",
        "tool_completed": "让模型读取工具结果并继续判断",
        "paused": "从最后 checkpoint 恢复",
        "completed": "任务已完成",
        "failed": "检查错误后决定重试或结束",
        "cancelled": "任务已取消",
    }.get(stage, "继续当前运行")


def _message_characters(messages: list[dict[str, Any]]) -> int:
    return sum(len(json.dumps(item, ensure_ascii=False, default=str)) for item in messages)


def _fit_character_budget(
    messages: list[dict[str, Any]],
    character_limit: int,
) -> list[dict[str, Any]]:
    if _message_characters(messages) <= character_limit:
        return messages
    fitted = [dict(item) for item in messages]
    # Preserve roles, tool-call relationships and the newest user intent. Only
    # textual content is shortened; full content remains durable in SQLite.
    for message in fitted:
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= 500:
            continue
        message["content"] = content[:500] + "…[已压缩，完整内容见 Checkpoint]"
        if _message_characters(fitted) <= character_limit:
            return fitted
    while len(fitted) > 3 and _message_characters(fitted) > character_limit:
        # Keep the first system message, Survival Summary and newest turn.
        fitted.pop(2)
    return fitted
