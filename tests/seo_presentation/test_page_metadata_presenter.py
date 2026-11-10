from src.models.common import Capability
from src.seo_presentation.registry import present_capability


def test_page_metadata_presenter_builds_plain_language_contract() -> None:
    result = present_capability(
        Capability.PAGE_METADATA,
        {
            "meta": {"capability": "page_metadata", "source": "page_html"},
            "data": {
                "source": "page_html",
                "is_estimated": False,
                "integrity": "complete",
                "requested_url": "https://example.com/",
                "final_url": "https://example.com/",
                "status_code": 200,
                "content_type": "text/html",
                "content_length_bytes": 1000,
                "title": "Example",
                "title_count": 1,
                "meta_description": "Description",
                "meta_description_count": 1,
                "meta_keywords": "seo",
                "meta_keywords_count": 1,
                "canonical_url": "https://example.com/",
                "canonical_count": 1,
                "robots": "index,follow",
                "declared_noindex": False,
                "html_lang": "en",
                "h1s": ["Main heading"],
                "h1_count": 1,
                "warnings": [],
                "analyzer_name": "python-seo-analyzer",
                "analyzer_version": "2025.4.3",
                "analyzer_status": "completed",
                "word_count": 88,
                "heading_counts": {"h1": 1, "h2": 2},
                "internal_links_count": 6,
                "image_missing_alt_count": 1,
                "generic_anchor_text_count": 0,
                "analyzer_warnings": ["Image missing alt tag: /hero.jpg"],
            },
        },
    )

    assert result is not None
    assert result["variant"] == "page_metadata"
    assert result["provenance"]["source_label"] == "页面源 HTML"
    assert result["checks"][0]["label"] == "Title"
    assert result["checks"][0]["status"] == "good"
    assert result["checks"][2]["status"] == "info"
    assert "Google 不使用" in result["checks"][2]["note"]
    assert result["analyzer"]["status"] == "completed"
    assert result["analyzer"]["word_count"] == 88
    assert result["analyzer"]["image_missing_alt_count"] == 1
    assert result["analyzer"]["warnings"] == ["Image missing alt tag: /hero.jpg"]
