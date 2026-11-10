from __future__ import annotations

import json
import re
from typing import Any

_REDACTED = "[REDACTED]"
_SENSITIVE = re.compile(
    r"(^|[_-])(api[_-]?key|token|secret|password|passwd|authorization|auth[_-]?code|cookie)($|[_-])",
    re.IGNORECASE,
)
_SAFE_TOKEN_METRICS = {"time_to_first_token_ms"}
_AUTH_VALUE = re.compile(
    r"(?i)(\bauthorization\b[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+)([^\s\"',;]+)"
)
_COOKIE_VALUE = re.compile(
    r"(?i)(\b(?:set-cookie|cookie)\b[\"']?\s*[:=]\s*[\"']?)([^\r\n\"']+)"
)
_SECRET_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|"
    r"auth[_-]?code|cookie)\b[\"']?\s*[:=]\s*[\"']?)([^\s\"',;&<>]+)"
)
_URL_KEY = re.compile(r"(?i)([?&]key=)([^&#\s]+)")
_PREFIXED_KEY = re.compile(r"\b(?:sk-|pk-lf-)[A-Za-z0-9_-]{8,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)


def sanitize_payload(
    value: Any, *, max_depth: int = 8, max_items: int = 100, max_string_length: int = 4096
) -> Any:
    return _sanitize(
        value, depth=0, max_depth=max_depth, max_items=max_items,
        max_string_length=max_string_length,
    )


def _sanitize(
    value: Any, *, depth: int, max_depth: int, max_items: int, max_string_length: int
) -> Any:
    if depth >= max_depth:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value
        if text.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                text = json.dumps(
                    _sanitize(
                        parsed, depth=depth + 1, max_depth=max_depth, max_items=max_items,
                        max_string_length=max_string_length,
                    ),
                    ensure_ascii=False, separators=(",", ":"),
                )
        text = _PRIVATE_KEY.sub(_REDACTED, text)
        for pattern in (_AUTH_VALUE, _COOKIE_VALUE, _SECRET_VALUE, _URL_KEY):
            text = pattern.sub(r"\1[REDACTED]", text)
        text = _PREFIXED_KEY.sub(_REDACTED, text)
        return (
            text if len(text) <= max_string_length
            else f"{text[:max_string_length]}…[TRUNCATED]"
        )
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= max_items:
                result["__truncated__"] = True
                break
            key = str(raw_key)
            result[key] = (
                _REDACTED
                if _SENSITIVE.search(key) and key not in _SAFE_TOKEN_METRICS
                else _sanitize(
                    item, depth=depth + 1, max_depth=max_depth, max_items=max_items,
                    max_string_length=max_string_length,
                )
            )
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [
            _sanitize(
                item, depth=depth + 1, max_depth=max_depth, max_items=max_items,
                max_string_length=max_string_length,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            result.append("[TRUNCATED]")
        return result
    if hasattr(value, "model_dump"):
        return _sanitize(
            value.model_dump(mode="json"),
            depth=depth + 1,
            max_depth=max_depth,
            max_items=max_items,
            max_string_length=max_string_length,
        )
    return _sanitize(
        str(value), depth=depth + 1, max_depth=max_depth, max_items=max_items,
        max_string_length=max_string_length,
    )


def sanitized_json(
    value: Any, *, max_depth: int = 8, max_items: int = 100, max_string_length: int = 4096
) -> str:
    return json.dumps(
        sanitize_payload(
            value, max_depth=max_depth, max_items=max_items, max_string_length=max_string_length
        ),
        ensure_ascii=False, separators=(",", ":"),
    )


def observation_json(value: Any) -> str:
    return sanitized_json(value, max_depth=12, max_string_length=50_000)
