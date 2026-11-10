from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.providers.page_metadata.client import FetchedHtmlPage
from src.providers.page_metadata.pyseo_client import (
    PythonSeoAnalyzerClient,
    PythonSeoAnalyzerConfig,
)


@pytest.mark.asyncio
async def test_python_seo_analyzer_client_sends_raw_html_to_isolated_runner(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text(
        """
import json
import sys
payload = json.loads(sys.stdin.read())
assert payload["url"] == "https://example.com/"
assert "<h1>Main</h1>" in payload["html"]
print(json.dumps({
    "analyzer_name": "python-seo-analyzer",
    "analyzer_version": "2025.4.3",
    "analyzer_status": "completed",
    "word_count": 2,
    "headings": {"h1": ["Main"]},
    "heading_counts": {"h1": 1},
    "internal_links_count": 0,
    "image_missing_alt_count": 0,
    "generic_anchor_text_count": 0,
    "analyzer_warnings": []
}))
""".strip()
    )
    config = PythonSeoAnalyzerConfig(
        enabled=True,
        python_executable=sys.executable,
        runner_path=runner,
        timeout=2.0,
        max_output_bytes=10_000,
    )
    client = PythonSeoAnalyzerClient(config)
    page = FetchedHtmlPage(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html",
        content_length_bytes=50,
        headers={},
        html="<html><body><h1>Main</h1></body></html>",
    )

    result = await client.analyze(page)

    assert result["analyzer_status"] == "completed"
    assert result["word_count"] == 2
    assert result["headings"] == {"h1": ["Main"]}
