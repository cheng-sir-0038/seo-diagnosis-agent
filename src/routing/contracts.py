"""Provider 路由的中立契约。

本模块只描述结构化查询要求、Provider 能力档案和选择结果，
不负责解析用户自然语言，也不负责执行实际 Provider 选择。
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from src.models.common import Capability, DataSource, Device


class QueryPurpose(enum.StrEnum):
    """一次查询的业务目的。"""

    QUICK_LOOKUP = "quick_lookup"
    SEO_ANALYSIS = "seo_analysis"
    RANK_MONITORING = "rank_monitoring"
    LOCAL_ANALYSIS = "local_analysis"
    RESEARCH = "research"


class ProviderFeature(enum.StrEnum):
    """路由时可确定性匹配的 Provider 能力标签。"""

    REALTIME = "realtime"
    COUNTRY_TARGETING = "country_targeting"
    LANGUAGE_TARGETING = "language_targeting"
    PAGINATION = "pagination"
    AUTOCORRECT = "autocorrect"
    DEVICE = "device"
    OS = "os"
    EXACT_LOCATION = "exact_location"
    COORDINATES = "coordinates"
    AI_OVERVIEW = "ai_overview"
    DEEP_RESULTS = "deep_results"


class ProviderOperatingSystem(enum.StrEnum):
    """搜索结果模拟设备的操作系统。"""

    WINDOWS = "windows"
    MACOS = "macos"
    ANDROID = "android"
    IOS = "ios"

    @property
    def device(self) -> Device:
        if self in {self.ANDROID, self.IOS}:
            return Device.MOBILE
        return Device.DESKTOP


class ProviderRuntimeMode(enum.StrEnum):
    """Provider 当前运行模式。"""

    LIVE = "live"
    PSEUDO = "pseudo"
    UNAVAILABLE = "unavailable"


class ProviderSelectionMode(enum.StrEnum):
    """Provider 选择结果的来源。"""

    AUTOMATIC = "automatic"
    PREFERRED = "preferred"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


class QueryIntent(BaseModel):
    """由 Agent 生成的结构化查询要求。

    该对象不保存用户原始自然语言，Provider Router 只消费这些稳定字段。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    purpose: QueryPurpose = QueryPurpose.QUICK_LOOKUP
    country: str = Field(default="us", min_length=2, max_length=2)
    language: str = Field(default="en", min_length=2, max_length=16)
    limit: int = Field(default=10, ge=1, le=1000)
    page: int = Field(default=1, ge=1, le=100)
    autocorrect: bool | None = None
    device: Device | None = None
    os: ProviderOperatingSystem | None = None
    location_name: str | None = None
    location_coordinate: str | None = None
    required_features: frozenset[ProviderFeature] = Field(default_factory=frozenset)
    preferred_provider: str | None = Field(default=None, min_length=1, max_length=80)
    allow_fallback: bool = True

    @model_validator(mode="before")
    @classmethod
    def infer_device_from_os(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        os_value = data.get("os")
        if os_value is not None and data.get("device") is None:
            operating_system = (
                os_value
                if isinstance(os_value, ProviderOperatingSystem)
                else ProviderOperatingSystem(str(os_value).strip().lower())
            )
            data["device"] = operating_system.device
        return data

    @field_validator("country", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> str:
        country = str(value or "").strip().lower()
        if len(country) != 2 or not country.isalpha():
            raise ValueError("country 必须是 ISO 3166-1 两位国家代码")
        return country

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: Any) -> str:
        language = str(value or "").strip().lower().replace("_", "-")
        if not language or not all(part.isalpha() for part in language.split("-")):
            raise ValueError("language 必须是有效语言代码")
        return language

    @field_validator("location_name", "location_coordinate", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("preferred_provider", mode="before")
    @classmethod
    def normalize_provider_name(cls, value: Any) -> str | None:
        if value is None:
            return None
        provider = str(value).strip().lower()
        return provider or None

    @model_validator(mode="after")
    def validate_and_derive_features(self) -> QueryIntent:
        if self.location_name and self.location_coordinate:
            raise ValueError("location_name 与 location_coordinate 不能同时提供")
        if self.os is not None and self.device != self.os.device:
            raise ValueError(f"{self.os.value} 只能与 {self.os.device.value} 设备一起使用")

        features = set(self.required_features)
        features.update(
            {
                ProviderFeature.COUNTRY_TARGETING,
                ProviderFeature.LANGUAGE_TARGETING,
            }
        )
        if self.device is not None:
            features.add(ProviderFeature.DEVICE)
        if self.os is not None:
            features.add(ProviderFeature.OS)
        if self.location_name:
            features.add(ProviderFeature.EXACT_LOCATION)
        if self.location_coordinate:
            features.add(ProviderFeature.COORDINATES)
        object.__setattr__(self, "required_features", frozenset(features))
        return self

    @field_serializer("required_features")
    def serialize_required_features(
        self, value: frozenset[ProviderFeature]
    ) -> list[str]:
        return sorted(item.value for item in value)


class ProviderProfile(BaseModel):
    """Provider 对路由器声明的能力档案。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_name: str = Field(min_length=1, max_length=80)
    source: DataSource
    runtime_mode: ProviderRuntimeMode
    capabilities: frozenset[Capability] = Field(default_factory=frozenset)
    supported_features: frozenset[ProviderFeature] = Field(default_factory=frozenset)
    implemented_features: frozenset[ProviderFeature] = Field(default_factory=frozenset)
    capability_limits: dict[Capability, int] = Field(default_factory=dict)
    fixed_language: str | None = Field(default=None, min_length=2, max_length=16)

    @field_validator("provider_name", mode="before")
    @classmethod
    def normalize_provider_name(cls, value: Any) -> str:
        provider = str(value or "").strip().lower()
        if not provider:
            raise ValueError("provider_name 不能为空")
        return provider


    @field_validator("fixed_language", mode="before")
    @classmethod
    def normalize_fixed_language(cls, value: Any) -> str | None:
        if value is None:
            return None
        language = str(value).strip().lower().replace("_", "-")
        return language or None

    @field_validator("capability_limits")
    @classmethod
    def validate_capability_limits(
        cls, value: dict[Capability, int]
    ) -> dict[Capability, int]:
        for capability, limit in value.items():
            if limit < 1:
                raise ValueError(f"{capability.value} 的最大结果数必须大于 0")
        return dict(value)

    def max_results_for(self, capability: Capability) -> int | None:
        return self.capability_limits.get(capability)

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def missing_features(
        self, required: frozenset[ProviderFeature] | set[ProviderFeature]
    ) -> frozenset[ProviderFeature]:
        """返回当前适配器实现尚不能满足的能力。"""
        return frozenset(set(required) - set(self.implemented_features))

    def upstream_missing_features(
        self, required: frozenset[ProviderFeature] | set[ProviderFeature]
    ) -> frozenset[ProviderFeature]:
        """返回上游供应商本身不支持的能力。"""
        return frozenset(set(required) - set(self.supported_features))

    @field_serializer("capabilities")
    def serialize_capabilities(self, value: frozenset[Capability]) -> list[str]:
        return sorted(item.value for item in value)

    @field_serializer("supported_features", "implemented_features")
    def serialize_supported_features(
        self, value: frozenset[ProviderFeature]
    ) -> list[str]:
        return sorted(item.value for item in value)


class ProviderDecision(BaseModel):
    """Provider Router 的可追踪选择结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: ProviderSelectionMode
    selected_provider: str | None = Field(default=None, max_length=80)
    selected_source: DataSource | None = None
    reasons: tuple[str, ...] = ()
    matched_features: frozenset[ProviderFeature] = Field(default_factory=frozenset)
    missing_features: frozenset[ProviderFeature] = Field(default_factory=frozenset)
    fallback_candidates: tuple[str, ...] = ()
    fallback_used: bool = False

    @field_validator("selected_provider", mode="before")
    @classmethod
    def normalize_selected_provider(cls, value: Any) -> str | None:
        if value is None:
            return None
        provider = str(value).strip().lower()
        return provider or None

    @field_validator("reasons", mode="before")
    @classmethod
    def normalize_reasons(cls, value: Any) -> tuple[str, ...]:
        return tuple(str(item).strip() for item in (value or ()) if str(item).strip())

    @field_validator("fallback_candidates", mode="before")
    @classmethod
    def normalize_fallback_candidates(cls, value: Any) -> tuple[str, ...]:
        return tuple(
            str(item).strip().lower()
            for item in (value or ())
            if str(item).strip()
        )

    @model_validator(mode="after")
    def validate_shape(self) -> ProviderDecision:
        if self.fallback_used and self.mode != ProviderSelectionMode.FALLBACK:
            raise ValueError("fallback_used=true 时 mode 必须是 fallback")
        if self.mode == ProviderSelectionMode.UNAVAILABLE and self.selected_provider:
            raise ValueError("unavailable 结果不能包含 selected_provider")
        if self.mode != ProviderSelectionMode.UNAVAILABLE and not self.selected_provider:
            raise ValueError("可用选择结果必须包含 selected_provider")
        if self.selected_provider is None and self.selected_source is not None:
            raise ValueError("没有 selected_provider 时不能包含 selected_source")
        return self

    @property
    def is_satisfied(self) -> bool:
        return self.selected_provider is not None and not self.missing_features

    @field_serializer("matched_features", "missing_features")
    def serialize_features(self, value: frozenset[ProviderFeature]) -> list[str]:
        return sorted(item.value for item in value)
