from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AdapterStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    NOT_IMPLEMENTED = "not_implemented"


class AdapterHealth(BaseModel):
    status: AdapterStatus
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AdapterResult(BaseModel):
    success: bool
    adapter_key: str
    operation: str
    external_reference: str | None = None
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class BaseAdapter(ABC):
    key: str
    category: str

    @abstractmethod
    async def health_check(self) -> AdapterHealth:
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> set[str]:
        raise NotImplementedError
