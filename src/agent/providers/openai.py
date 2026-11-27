from __future__ import annotations

from typing import Any

from src.agent.providers.chat_completions import ChatCompletionsProvider
from src.core.config import DEFAULT_OPENAI_MODEL


class OpenAIProvider(ChatCompletionsProvider):
    """Stream OpenAI Chat Completions using the workbench tool protocol."""

    def __init__(
        self, *, api_key: str, base_url: str = "https://api.openai.com/v1",
        model: str = DEFAULT_OPENAI_MODEL, timeout: float = 60.0,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, model=model, timeout=timeout)

    def build_payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        # A tool reply carries its call ID and content. The tool name is local bookkeeping.
        outbound = [
            {field: message[field] for field in ("role", "tool_call_id", "content")}
            if message.get("role") == "tool" else message
            for message in messages
        ]
        # Existing tools have optional fields and defaults outside OpenAI's strict subset.
        # The executor still validates tool arguments through the application tool contract.
        functions = [{**tool, "function": {**tool["function"], "strict": False}} for tool in tools]
        return super().build_payload(outbound, functions, tool_choice=tool_choice)
