from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1.0"

_SOURCE_LABELS = {
    "dataforseo": "DataForSEO",
    "serper": "Serper",
    "ahrefs": "Ahrefs",
    "semrush": "Semrush",
    "gsc": "Google Search Console",
    "google_search_console": "Google Search Console",
    "pagespeed": "Google PageSpeed Insights",
    "pagespeed_insights": "Google PageSpeed Insights",
    "page_html": "页面源 HTML",
}


def localized(key: str, fallback: str) -> dict[str, str]:
    return {"key": key, "fallback": fallback}


def json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_value(item) for item in value]
    return json.dumps(value, ensure_ascii=False, default=str)


def mapping_rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in data if isinstance(item, Mapping)]


def build_provenance(response: Mapping[str, Any]) -> dict[str, Any]:
    meta = response.get("meta") if isinstance(response.get("meta"), Mapping) else {}
    data = response.get("data")
    evidence: Mapping[str, Any] = {}
    if isinstance(data, Mapping):
        evidence = data
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        evidence = next((item for item in data if isinstance(item, Mapping)), {})

    country = evidence.get("country")
    if isinstance(country, Mapping):
        country = country.get("code") or country.get("name")

    source = evidence.get("source") or meta.get("source")
    source_text = _text(source)
    return {
        "source": source_text,
        "source_label": _SOURCE_LABELS.get((source_text or "").lower(), source_text),
        "is_estimated": bool(evidence.get("is_estimated", False)),
        "integrity": _text(evidence.get("integrity")),
        "country": str(country).upper() if country else None,
        "fetched_at": _text(evidence.get("fetched_at")),
    }


def base_contract(
    *,
    capability: str,
    view_type: str,
    title_key: str,
    title_fallback: str,
    description_key: str,
    description_fallback: str,
    response: Mapping[str, Any],
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "capability": capability,
        "view_type": view_type,
        "title": localized(title_key, title_fallback),
        "description": localized(description_key, description_fallback),
        "provenance": build_provenance(response),
        "limitations": list(limitations or []),
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
