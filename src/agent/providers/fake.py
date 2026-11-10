from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from src.agent.providers.base import ModelChunk, ModelProvider, ToolCall


class FakeModelProvider(ModelProvider):
    def __init__(self, turns: list[list[ModelChunk]]) -> None:
        self._turns = turns
        self.calls = 0
        self.tool_choices: list[str] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    @classmethod
    def text(cls, text: str) -> FakeModelProvider:
        return cls(
            [
                [
                    ModelChunk(text=part)
                    for part in [text[: max(1, len(text) // 2)], text[max(1, len(text) // 2) :]]
                    if part
                ]
                + [ModelChunk(finish_reason="stop")]
            ]
        )

    @classmethod
    def calculator_then_text(cls, expression: str, text: str) -> FakeModelProvider:
        return cls(
            [
                [
                    ModelChunk(
                        tool_calls=[
                            ToolCall(
                                id="call_calc",
                                name="calculator",
                                arguments={"expression": expression},
                            )
                        ],
                        finish_reason="tool_calls",
                    )
                ],
                [ModelChunk(text=text), ModelChunk(finish_reason="stop")],
            ]
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
    ) -> AsyncIterator[ModelChunk]:
        self.tool_choices.append(tool_choice)
        index = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for chunk in self._turns[index]:
            yield chunk
