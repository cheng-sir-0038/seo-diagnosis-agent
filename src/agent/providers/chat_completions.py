from __future__ import annotations

import asyncio
import codecs
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from src.agent.providers.base import ModelChunk, ModelProvider, ToolCall
from src.agent.providers.errors import ModelConfigurationError, ModelRequestError
from src.core.config import public_model_id


class CompletionStream:
    """Collect the fragments for each tool, in the order requested by the model."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key
        self.model: str | None = None
        self.tool_parts: dict[int, dict[str, str]] = defaultdict(
            lambda: {"id": "", "name": "", "arguments": ""}
        )

    def parse(self, payload: dict[str, Any]) -> list[ModelChunk]:
        emitted: list[ModelChunk] = []
        model = public_model_id(payload.get("model"), self.api_key)
        if model is not None and model != self.model:
            self.model = model
            emitted.append(ModelChunk(model=model))
        usage = payload.get("usage") or {}
        if usage:
            details = usage.get("completion_tokens_details") or {}
            cached = usage.get("prompt_tokens_details") or {}
            counts = {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cached_tokens": cached.get("cached_tokens", usage.get("prompt_cache_hit_tokens")),
                "reasoning_tokens": details.get("reasoning_tokens"),
            }
            if any(value is not None for value in counts.values()):
                emitted.append(ModelChunk(usage={
                    key: int(value) for key, value in counts.items() if value is not None
                }))
        choices = payload.get("choices") or []
        if not choices:
            return emitted
        choice = choices[0]
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("模型文本增量格式无效")
        if content:
            emitted.append(ModelChunk(text=content))
        for item in delta.get("tool_calls") or []:
            index = int(item.get("index", 0))
            part = self.tool_parts[index]
            if item.get("id"):
                part["id"] = str(item["id"])
            function = item.get("function") or {}
            if function.get("name"):
                part["name"] += str(function["name"])
            if function.get("arguments"):
                part["arguments"] += str(function["arguments"])
        finish_reason = choice.get("finish_reason")
        if finish_reason == "tool_calls":
            calls: list[ToolCall] = []
            for index in sorted(self.tool_parts):
                part = self.tool_parts[index]
                try:
                    arguments = json.loads(part["arguments"] or "{}")
                except json.JSONDecodeError:
                    raise ValueError("工具参数不是有效 JSON") from None
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须是 JSON 对象")
                if not part["id"] or not part["name"]:
                    raise ValueError("工具调用缺少 ID 或名称")
                calls.append(ToolCall(id=part["id"], name=part["name"], arguments=arguments))
            if not calls:
                raise ValueError("模型未提供工具调用")
            emitted.append(ModelChunk(tool_calls=calls, finish_reason="tool_calls"))
            self.tool_parts.clear()
        elif finish_reason is not None:
            if self.tool_parts or finish_reason not in ("stop", "length", "content_filter"):
                raise ValueError("模型响应的结束状态无效")
            emitted.append(ModelChunk(finish_reason=finish_reason))
        return emitted


class ChatCompletionsProvider(ModelProvider):
    """Shared HTTP transport for OpenAI-compatible Chat Completions streams."""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float) -> None:
        if not api_key.strip():
            raise ModelConfigurationError(f"{self.provider_name.upper()}_API_KEY 未配置")
        self._api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._parser = CompletionStream(self._api_key)

    @property
    def model_name(self) -> str:
        return self.model

    def build_payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice)
        return payload

    def parse_payload(self, payload: dict[str, Any]) -> list[ModelChunk]:
        return self._parser.parse(payload)

    def request_error(self, status_code: int | None, *, reason: str) -> ModelRequestError:
        return ModelRequestError(self.provider_name, status_code, reason=reason)

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
        tool_choice: str = "auto",
    ) -> AsyncIterator[ModelChunk]:
        """Yield tokens, completed tool calls, and usage. Never include raw error bodies."""
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "text/event-stream"}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        parser = CompletionStream(self._api_key)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/chat/completions", headers=headers, allow_redirects=False,
                    json=self.build_payload(messages, tools, tool_choice=tool_choice),
                ) as response:
                    if not 200 <= response.status < 300:
                        reason = "unavailable"
                        if response.status == 429:
                            reason = await _quota_reason(response.content)
                        raise self.request_error(response.status, reason=reason)
                    finished = False
                    async for data in _sse_data(response):
                        if data == "[DONE]":
                            if not finished:
                                raise self.request_error(None, reason="invalid_response")
                            return
                        payload = json.loads(data)
                        if payload.get("error"):
                            raise self.request_error(None, reason="invalid_response")
                        choices = payload.get("choices") or []
                        if choices and choices[0].get("finish_reason") == "insufficient_system_resource":
                            raise self.request_error(None, reason="unavailable")
                        for chunk in parser.parse(payload):
                            finished |= chunk.finish_reason is not None
                            yield chunk
                    raise self.request_error(None, reason="invalid_response")
        except TimeoutError:
            raise self.request_error(None, reason="timeout") from None
        except aiohttp.ClientError:
            raise self.request_error(None, reason="unavailable") from None
        except (ValueError, TypeError, AttributeError, IndexError):
            raise self.request_error(None, reason="invalid_response") from None


async def _sse_data(response: aiohttp.ClientResponse) -> AsyncIterator[str]:
    buffer = ""
    decoder = codecs.getincrementaldecoder("utf-8")()
    async for raw in response.content.iter_any():
        buffer += decoder.decode(raw)
        buffer = buffer.replace("\r\n", "\n")
        frames = buffer.split("\n\n")
        buffer = frames.pop()
        for frame in frames:
            lines = [line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:")]
            if lines:
                yield "\n".join(lines)


async def _quota_reason(content: aiohttp.StreamReader) -> str:
    try:
        body = await content.readexactly(4096)
    except asyncio.IncompleteReadError as exc:
        body = exc.partial
    except (TimeoutError, aiohttp.ClientError):
        return "rate_limit"
    try:
        if json.loads(body).get("error", {}).get("code") == "insufficient_quota":
            return "insufficient_quota"
    except (ValueError, AttributeError):
        pass
    return "rate_limit"
