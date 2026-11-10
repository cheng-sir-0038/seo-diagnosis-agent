from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StructuredInvocationResult:
    parsed: Any
    raw: Any = None
    parsing_error: BaseException | None = None
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None


async def invoke_structured_model(
    model: Any,
    schema: Any,
    messages: list[Any],
) -> StructuredInvocationResult:
    """调用结构化模型并尽量保留原始 AIMessage 与用量。

    LangChain 模型支持 ``include_raw=True`` 时，返回 raw/parsed/parsing_error；
    简单 Fake 或兼容实现若直接返回解析值，也可以继续使用。
    """

    try:
        runnable = model.with_structured_output(schema, include_raw=True)
    except TypeError:
        runnable = model.with_structured_output(schema)
    result = await runnable.ainvoke(messages)
    if (
        isinstance(result, dict)
        and "parsed" in result
        and ("raw" in result or "parsing_error" in result)
    ):
        raw = result.get("raw")
        parsed = result.get("parsed")
        parsing_error = result.get("parsing_error")
    else:
        raw = None
        parsed = result
        parsing_error = None
    return StructuredInvocationResult(
        parsed=parsed,
        raw=raw,
        parsing_error=parsing_error,
        usage=_extract_usage(raw),
        finish_reason=_extract_finish_reason(raw),
    )


def _extract_usage(raw: Any) -> dict[str, int]:
    if raw is None:
        return {}
    usage = dict(getattr(raw, "usage_metadata", None) or {})
    response_metadata = dict(getattr(raw, "response_metadata", None) or {})
    token_usage = dict(response_metadata.get("token_usage") or {})
    input_details = dict(usage.get("input_token_details") or {})
    output_details = dict(usage.get("output_token_details") or {})
    input_tokens = int(usage.get("input_tokens") or token_usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or token_usage.get("completion_tokens") or 0)
    total_tokens = int(
        usage.get("total_tokens")
        or token_usage.get("total_tokens")
        or (input_tokens + output_tokens)
    )
    cached_tokens = int(
        input_details.get("cache_read") or token_usage.get("prompt_cache_hit_tokens") or 0
    )
    reasoning_tokens = int(
        output_details.get("reasoning")
        or dict(token_usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        or 0
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _extract_finish_reason(raw: Any) -> str | None:
    if raw is None:
        return None
    metadata = dict(getattr(raw, "response_metadata", None) or {})
    reason = metadata.get("finish_reason")
    return str(reason) if reason is not None else None
