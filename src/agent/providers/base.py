from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ModelChunk:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class ModelProvider(ABC):
    @property
    def provider_name(self) -> str:
        return self.__class__.__name__.removesuffix("Provider").lower()

    @property
    def model_name(self) -> str | None:
        return None

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
    ) -> AsyncIterator[ModelChunk]:
        raise NotImplementedError
