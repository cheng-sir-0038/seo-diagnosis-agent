from src.providers.page_metadata.client import FetchedHtmlPage
from src.providers.page_metadata.provider import parse_page_metadata


def parse(html):
    return parse_page_metadata(FetchedHtmlPage(
        requested_url="https://example.com/guide", final_url="https://example.com/guide",
        status_code=200, content_type="text/html", content_length_bytes=len(html),
        headers={}, html=html,
    ))


def test_collects_bounded_source_text_and_existing_structured_data_without_script_text():
    page = parse('''<html><head><title>Waterjet guide</title>
      <meta property="og:image" content="/guide.jpg">
      <meta name="author" content="Lin">
      <meta name="google-site-verification" content="exclude-this-value">
      <script type="application/ld+json">{"@type":"Article","headline":"Existing"}</script>
      </head><body><h1>Waterjet guide</h1><p>Written by Lin.</p>
      <script>ignore-script</script><style>ignore-style</style>
      <template>ignore-template</template><noscript>ignore-noscript</noscript></body></html>''')
    assert page["content_excerpt"] == "Waterjet guide Written by Lin."
    assert page["og_image"] == "https://example.com/guide.jpg"
    assert page["meta_tags"] == {"og:image": "/guide.jpg", "author": "Lin"}
    assert page["existing_json_ld"] == [{"@type": "Article", "headline": "Existing"}]
    assert not page["content_excerpt_truncated"]


def test_marks_truncated_text_and_invalid_json_ld_without_failing_metadata_read():
    page = parse('<script type="application/ld+json">broken</script><body>' + 'x' * 7000)
    assert len(page["content_excerpt"]) <= 6000
    assert page["content_excerpt_truncated"]
    assert page["existing_json_ld"] == []
    assert any("JSON-LD" in warning for warning in page["warnings"])
