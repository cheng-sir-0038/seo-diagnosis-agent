from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.models.common import DataSource
from src.providers.page_metadata import FetchedHtmlPage, PageMetadataProvider


@pytest.mark.asyncio
async def test_page_metadata_provider_extracts_tdk_canonical_robots_and_h1() -> None:
    client = type(
        "Client",
        (),
        {
            "fetch": AsyncMock(),
            "startup": AsyncMock(),
            "shutdown": AsyncMock(),
            "_session": object(),
        },
    )()
    client.fetch.return_value = FetchedHtmlPage(
        requested_url="https://example.com/",
        final_url="https://example.com/home",
        status_code=200,
        content_type="text/html; charset=utf-8",
        content_length_bytes=500,
        headers={"x-robots-tag": "all"},
        html="""<!doctype html><html lang="zh-CN"><head>
        <meta charset="utf-8"><title>示例首页</title>
        <meta name="description" content="这是首页描述">
        <meta name="keywords" content="seo,agent">
        <meta name="robots" content="index,follow">
        <link rel="canonical" href="/canonical-home">
        <meta property="og:title" content="社交标题">
        </head><body><h1>首页主标题</h1></body></html>""",
    )
    provider = PageMetadataProvider(client=client)

    report = await provider.get_page_metadata("example.com")

    assert report.source == DataSource.PAGE_HTML
    assert report.title == "示例首页"
    assert report.meta_description == "这是首页描述"
    assert report.meta_keywords == "seo,agent"
    assert report.canonical_url == "https://example.com/canonical-home"
    assert report.robots == "index,follow"
    assert report.h1s == ["首页主标题"]
    assert report.html_lang == "zh-CN"
    assert report.charset == "utf-8"
    assert report.declared_noindex is False
    assert report.is_estimated is False


@pytest.mark.asyncio
async def test_page_metadata_provider_preserves_missing_and_duplicate_evidence() -> None:
    client = type(
        "Client",
        (),
        {
            "fetch": AsyncMock(),
            "startup": AsyncMock(),
            "shutdown": AsyncMock(),
            "_session": object(),
        },
    )()
    client.fetch.return_value = FetchedHtmlPage(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=404,
        content_type="text/html",
        content_length_bytes=100,
        headers={"x-robots-tag": "noindex"},
        html="<html><head><title>A</title><title>B</title></head><body><h1>One</h1><h1>Two</h1></body></html>",
    )
    report = await PageMetadataProvider(client=client).get_page_metadata("example.com")

    assert report.title == "A"
    assert report.title_count == 2
    assert report.meta_description is None
    assert report.h1_count == 2
    assert report.declared_noindex is True
    assert any("HTTP 404" in item for item in report.warnings)
    assert any("多个 Title" in item for item in report.warnings)


@pytest.mark.asyncio
async def test_page_metadata_counts_empty_description_tag_as_present_but_empty() -> None:
    client = type(
        "Client",
        (),
        {
            "fetch": AsyncMock(),
            "startup": AsyncMock(),
            "shutdown": AsyncMock(),
            "_session": object(),
        },
    )()
    client.fetch.return_value = FetchedHtmlPage(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html",
        content_length_bytes=100,
        headers={},
        html='<html><head><title>Example</title><meta name="description" content=""></head></html>',
    )

    report = await PageMetadataProvider(client=client).get_page_metadata("example.com")

    assert report.meta_description is None
    assert report.meta_description_count == 1
    assert any("没有非空 Meta Description" in item for item in report.warnings)


@pytest.mark.asyncio
async def test_page_metadata_provider_merges_python_seo_analyzer_evidence() -> None:
    client = type(
        "Client",
        (),
        {
            "fetch": AsyncMock(),
            "startup": AsyncMock(),
            "shutdown": AsyncMock(),
            "_session": object(),
        },
    )()
    client.fetch.return_value = FetchedHtmlPage(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html",
        content_length_bytes=300,
        headers={},
        html=(
            '<html><head><title>Example</title><meta name="description" content="Desc">'
            "</head><body><h1>Main</h1><h2>Section</h2></body></html>"
        ),
    )
    analyzer = type("Analyzer", (), {"analyze": AsyncMock(), "enabled": True})()
    analyzer.analyze.return_value = {
        "analyzer_name": "python-seo-analyzer",
        "analyzer_version": "2025.4.3",
        "analyzer_status": "completed",
        "word_count": 321,
        "content_hash": "sha1-value",
        "headings": {"h1": ["Main"], "h2": ["Section"]},
        "heading_counts": {"h1": 1, "h2": 1},
        "internal_links_count": 4,
        "image_missing_alt_count": 2,
        "generic_anchor_text_count": 1,
        "analyzer_warnings": ["Image missing alt tag: /hero.jpg"],
    }

    report = await PageMetadataProvider(client=client, analyzer=analyzer).get_page_metadata(
        "example.com"
    )

    analyzer.analyze.assert_awaited_once_with(client.fetch.return_value)
    assert report.analyzer_name == "python-seo-analyzer"
    assert report.analyzer_version == "2025.4.3"
    assert report.analyzer_status == "completed"
    assert report.word_count == 321
    assert report.heading_counts == {"h1": 1, "h2": 1}
    assert report.internal_links_count == 4
    assert report.image_missing_alt_count == 2
    assert report.analyzer_warnings == ["Image missing alt tag: /hero.jpg"]
    assert report.warnings == []


@pytest.mark.asyncio
async def test_page_metadata_provider_keeps_metadata_when_python_seo_analyzer_fails() -> None:
    client = type(
        "Client",
        (),
        {
            "fetch": AsyncMock(),
            "startup": AsyncMock(),
            "shutdown": AsyncMock(),
            "_session": object(),
        },
    )()
    client.fetch.return_value = FetchedHtmlPage(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html",
        content_length_bytes=100,
        headers={},
        html=(
            '<html><head><title>Example</title><meta name="description" content="Desc">'
            "</head><body><h1>Main</h1></body></html>"
        ),
    )
    analyzer = type("Analyzer", (), {"analyze": AsyncMock(), "enabled": True})()
    analyzer.analyze.side_effect = RuntimeError("worker down")

    report = await PageMetadataProvider(client=client, analyzer=analyzer).get_page_metadata(
        "example.com"
    )

    assert report.title == "Example"
    assert report.meta_description == "Desc"
    assert report.analyzer_status == "failed"
    assert report.analyzer_error == "python-seo-analyzer 分析失败"
    assert report.analyzer_warnings == []
    assert all("worker down" not in warning for warning in report.warnings)
