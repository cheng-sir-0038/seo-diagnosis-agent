from __future__ import annotations

from pydantic import BaseModel, Field

from src.seo_agent.adapters.base import BaseAdapter


class AdapterDefinition(BaseModel):
    category: str
    key: str
    display_name: str
    implemented: bool
    capabilities: set[str] = Field(default_factory=set)


class AdapterRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], AdapterDefinition] = {}
        self._adapters: dict[tuple[str, str], BaseAdapter] = {}

    def register_definition(self, definition: AdapterDefinition) -> None:
        identity = (definition.category, definition.key)
        self._definitions[identity] = definition

    def register(self, adapter: BaseAdapter) -> None:
        identity = (adapter.category, adapter.key)
        definition = self._definitions.get(identity)
        if definition is None:
            raise KeyError(f"适配器定义不存在: {adapter.category}/{adapter.key}")
        if not definition.implemented:
            raise ValueError(f"占位适配器不能注册实现: {adapter.category}/{adapter.key}")
        self._adapters[identity] = adapter

    def resolve(self, category: str, key: str) -> BaseAdapter:
        identity = (category, key)
        definition = self._definitions.get(identity)
        if definition is None:
            raise KeyError(f"适配器定义不存在: {category}/{key}")
        if not definition.implemented:
            raise NotImplementedError(f"NOT_IMPLEMENTED: {category}/{key}")
        adapter = self._adapters.get(identity)
        if adapter is None:
            raise RuntimeError(f"适配器尚未注册: {category}/{key}")
        return adapter

    def list_definitions(self) -> list[AdapterDefinition]:
        return sorted(self._definitions.values(), key=lambda item: (item.category, item.key))
