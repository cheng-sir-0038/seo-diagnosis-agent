from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.models.common import Capability
from src.seo_presentation.contracts import base_contract, json_value, mapping_rows
from src.seo_presentation.field_catalog import field_contract


@dataclass(frozen=True, slots=True)
class TablePresentationSpec:
    capability: Capability
    title_key: str
    title: str
    description_key: str
    description: str
    fields: tuple[str, ...]
    limitations: tuple[str, ...] = ()


class CatalogTablePresenter:
    def __init__(self, spec: TablePresentationSpec) -> None:
        self.spec = spec

    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        rows = mapping_rows(response.get("data"))
        if not rows:
            return None
        contract = base_contract(
            capability=self.spec.capability.value,
            view_type="table",
            title_key=self.spec.title_key,
            title_fallback=self.spec.title,
            description_key=self.spec.description_key,
            description_fallback=self.spec.description,
            response=response,
            limitations=list(self.spec.limitations),
        )
        contract["columns"] = [field_contract(key) for key in self.spec.fields]
        contract["rows"] = [
            {key: json_value(row.get(key)) for key in self.spec.fields} for row in rows
        ]
        return contract
