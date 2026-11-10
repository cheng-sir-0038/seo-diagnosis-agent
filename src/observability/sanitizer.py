from __future__ import annotations

import json
import re
from typing import Any

_REDACTED = "[REDACTED]"
_SENSITIVE = re.compile(
    r"(^|_)(api[_-]?key|token|secret|password|passwd|authorization|auth[_-]?code|cookie)($|_)",
    re.IGNORECASE,
)


def sanitize_payload(value: Any, *, max_depth: int = 8, max_items: int = 100) -> Any:
    return _sanitize(value, depth=0, max_depth=max_depth, max_items=max_items)


def _sanitize(value: Any, *, depth: int, max_depth: int, max_items: int) -> Any:
    if depth >= max_depth:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 4096 else f"{value[:4096]}…[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= max_items:
                result["__truncated__"] = True
                break
            key = str(raw_key)
            result[key] = (
                _REDACTED
                if _SENSITIVE.search(key)
                else _sanitize(item, depth=depth + 1, max_depth=max_depth, max_items=max_items)
            )
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [
            _sanitize(item, depth=depth + 1, max_depth=max_depth, max_items=max_items)
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
        )
    return _sanitize(str(value), depth=depth + 1, max_depth=max_depth, max_items=max_items)


def sanitized_json(value: Any) -> str:
    return json.dumps(sanitize_payload(value), ensure_ascii=False, separators=(",", ":"))
