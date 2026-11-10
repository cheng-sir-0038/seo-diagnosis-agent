from __future__ import annotations

import codecs
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from src.agent.providers.base import ModelChunk, ModelProvider, ToolCall


class DeepSeekRequestError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"DeepSeek 请求失败: HTTP {status_code}")


class DeepSeekProvider(ModelProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 未配置")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._tool_parts: dict[int, dict[str, str]] = defaultdict(
            lambda: {"id": "", "name": "", "arguments": ""}
        )

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def model_name(self) -> str:
        return self.model

    def build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        return payload

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
    ) -> AsyncIterator[ModelChunk]:
        self._tool_parts.clear()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=self.build_payload(messages, tools, tool_choice=tool_choice),
            ) as response:
                if response.status >= 400:
                    await response.read()
                    raise DeepSeekRequestError(response.status)
                buffer = ""
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                async for raw in response.content.iter_any():
                    buffer += decoder.decode(raw)
                    frames = buffer.split("\n\n")
                    buffer = frames.pop() or ""
                    for frame in frames:
                        for line in frame.splitlines():
                            line = line.strip()
                            if not line or line.startswith(":") or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            payload = json.loads(data)
                            for chunk in self.parse_payload(payload):
                                yield chunk

    def parse_payload(self, payload: dict[str, Any]) -> list[ModelChunk]:
        emitted: list[ModelChunk] = []
        usage = payload.get("usage") or {}
        if usage:
            details = usage.get("completion_tokens_details") or {}
            counts = {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cached_tokens": usage.get("prompt_cache_hit_tokens"),
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
        if content:
            emitted.append(ModelChunk(text=str(content)))
        for item in delta.get("tool_calls") or []:
            index = int(item.get("index", 0))
            part = self._tool_parts[index]
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
            for index in sorted(self._tool_parts):
                part = self._tool_parts[index]
                try:
                    arguments = json.loads(part["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise ValueError("工具参数不是有效 JSON") from exc
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须是 JSON 对象")
                calls.append(
                    ToolCall(
                        id=part["id"] or f"call_{index}",
                        name=part["name"],
                        arguments=arguments,
                    )
                )
            emitted.append(ModelChunk(tool_calls=calls, finish_reason="tool_calls"))
            self._tool_parts.clear()
        elif finish_reason:
            emitted.append(ModelChunk(finish_reason=str(finish_reason)))
        return emitted
