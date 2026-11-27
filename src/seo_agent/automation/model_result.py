from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import SecretStr

from src.agent.providers.errors import ModelRequestError
from src.core.config import public_model_id


def model_provider_name(model: Any) -> str:
    match getattr(model, "_llm_type", None):
        case "chat-deepseek":
            return "deepseek"
        case "openai-chat":
            return "openai"
        case _:
            return str(getattr(model, "provider_name", model.__class__.__name__)).lower()


def _request_error(provider: str, error: Exception) -> ModelRequestError:
    if isinstance(error, APIStatusError):
        reason = "insufficient_quota" if error.code == "insufficient_quota" else "unavailable"
        return ModelRequestError(provider, error.status_code, reason=reason)
    if isinstance(error, (APITimeoutError, TimeoutError)):
        return ModelRequestError(provider, reason="timeout")
    if isinstance(error, APIConnectionError):
        return ModelRequestError(provider)
    return ModelRequestError(provider, reason="invalid_response")


@dataclass(slots=True)
class StructuredInvocationResult:
    parsed: Any
    raw: Any = None
    parsing_error: BaseException | None = None
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    model_name: str | None = None


async def invoke_structured_model(
    model: Any,
    schema: Any,
    messages: list[Any],
    *,
    method: str | None = None,
) -> StructuredInvocationResult:
    """调用结构化模型并尽量保留原始 AIMessage 与用量。

    LangChain 模型支持 ``include_raw=True`` 时，返回 raw/parsed/parsing_error；
    简单 Fake 或兼容实现若直接返回解析值，也可以继续使用。
    """

    provider = model_provider_name(model)
    known_provider = provider in {"openai", "deepseek"}
    if provider == "openai" and method is None:
        method = "function_calling"
    try:
        try:
            runnable = model.with_structured_output(
                schema, include_raw=True, **({"method": method} if method else {})
            )
        except TypeError:
            if known_provider:
                raise
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
            raw, parsed, parsing_error = None, result, None
        if known_provider and (parsing_error is not None or parsed is None):
            parsing_error = ModelRequestError(provider, reason="invalid_response")
        elif parsed is None and parsing_error is None:
            parsing_error = ValueError("模型未返回可解析的结构化结果")
        return StructuredInvocationResult(
            parsed=parsed,
            raw=raw,
            parsing_error=parsing_error,
            usage=_extract_usage(raw),
            finish_reason=_extract_finish_reason(raw),
            model_name=_extract_model_name(raw, model),
        )
    except Exception as error:
        if known_provider:
            raise _request_error(provider, error) from None
        raise


def _extract_model_name(raw: Any, model: Any) -> str | None:
    metadata = getattr(raw, "response_metadata", None) or {}
    key = getattr(model, "openai_api_key", None) or getattr(model, "api_key", None)
    if isinstance(key, SecretStr):
        key = key.get_secret_value()
    return public_model_id(metadata.get("model_name"), key if isinstance(key, str) else "")


def _extract_usage(raw: Any) -> dict[str, int]:
    if raw is None:
        return {}
    usage = dict(getattr(raw, "usage_metadata", None) or {})
    response_metadata = dict(getattr(raw, "response_metadata", None) or {})
    token_usage = dict(response_metadata.get("token_usage") or {})
    if not usage and not token_usage:
        return {}
    input_details = dict(usage.get("input_token_details") or {})
    output_details = dict(usage.get("output_token_details") or {})
    candidates = {
        "input_tokens": (usage.get("input_tokens"), token_usage.get("prompt_tokens")),
        "output_tokens": (usage.get("output_tokens"), token_usage.get("completion_tokens")),
        "total_tokens": (usage.get("total_tokens"), token_usage.get("total_tokens")),
        "cached_tokens": (
            input_details.get("cache_read"), token_usage.get("prompt_cache_hit_tokens")
        ),
        "reasoning_tokens": (
            output_details.get("reasoning"),
            dict(token_usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        ),
    }
    counts = {
        key: int(next(value for value in values if value is not None))
        for key, values in candidates.items() if any(value is not None for value in values)
    }
    if "total_tokens" not in counts and "input_tokens" in counts and "output_tokens" in counts:
        counts["total_tokens"] = counts["input_tokens"] + counts["output_tokens"]
    return counts


def _extract_finish_reason(raw: Any) -> str | None:
    if raw is None:
        return None
    metadata = dict(getattr(raw, "response_metadata", None) or {})
    reason = metadata.get("finish_reason")
    if reason in ("stop", "length", "tool_calls", "content_filter", "insufficient_system_resource"):
        return reason
    return None
