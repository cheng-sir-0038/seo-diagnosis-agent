"""运行在 python-seo-analyzer 隔离环境中的最小 JSON runner。"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from collections.abc import Mapping
from typing import Any


def run(payload: Mapping[str, Any]) -> dict[str, Any]:
    from pyseoanalyzer.page import Page

    url = str(payload.get("url") or "")
    base_domain = str(payload.get("base_domain") or "")
    html = str(payload.get("html") or "")
    if not url or not base_domain or not html:
        raise ValueError("url、base_domain 和 html 不能为空")

    page = Page(
        url=url,
        base_domain=base_domain,
        analyze_headings=True,
        analyze_extra_tags=True,
        run_llm_analysis=False,
    )
    page.analyze(raw_html=html)
    raw = page.as_dict()
    warnings = [str(item) for item in raw.get("warnings") or []]
    headings = _normalize_headings(raw.get("headings"))
    return {
        "analyzer_name": "python-seo-analyzer",
        "analyzer_version": _package_version(),
        "analyzer_status": "completed",
        "word_count": _non_negative_int(raw.get("word_count")),
        "content_hash": _optional_text(raw.get("content_hash")),
        "headings": headings,
        "heading_counts": {key: len(value) for key, value in headings.items()},
        "internal_links_count": len({str(item) for item in page.links if item}),
        "image_missing_alt_count": sum(
            item.startswith("Image missing alt tag:") for item in warnings
        ),
        "generic_anchor_text_count": sum(
            item.startswith("Anchor text contains generic text:") for item in warnings
        ),
        "analyzer_warnings": warnings,
    }


def _normalize_headings(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, list[str]] = {}
    for key in ("h1", "h2", "h3", "h4", "h5", "h6"):
        items = value.get(key)
        if isinstance(items, list):
            result[key] = [str(item).strip() for item in items if str(item).strip()]
    return result


def _non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _package_version() -> str | None:
    try:
        return importlib.metadata.version("pyseoanalyzer")
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("输入必须是 JSON 对象")
        result = run(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # runner 边界只向主进程返回异常类型
        sys.stderr.write(f"{type(exc).__name__}: analysis failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
