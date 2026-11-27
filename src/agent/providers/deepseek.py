from __future__ import annotations

from typing import Any

from src.agent.providers.chat_completions import ChatCompletionsProvider
from src.agent.providers.errors import ModelRequestError


class DeepSeekRequestError(ModelRequestError):
    def __init__(self, status_code: int | None, *, reason: str = "unavailable") -> None:
        super().__init__("deepseek", status_code, reason=reason)


class DeepSeekProvider(ChatCompletionsProvider):
    def __init__(
        self, *, api_key: str, base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash", timeout: float = 60.0,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, model=model, timeout=timeout)

    def build_payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        payload = super().build_payload(messages, tools, tool_choice=tool_choice)
        payload["thinking"] = {"type": "disabled"}
        return payload

    def request_error(self, status_code: int | None, *, reason: str) -> ModelRequestError:
        return DeepSeekRequestError(status_code, reason=reason)
