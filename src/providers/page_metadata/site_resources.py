from __future__ import annotations

from xml.etree import ElementTree

from src.core.exceptions import InternalAdapterError
from src.models.common import DataIntegrity, DataSource
from src.models.page_metadata import SiteResourceKind, SiteResourceReport
from src.providers.page_metadata.client import (
    FetchedSiteResource,
    normalize_site_url,
    site_origin,
)

SAMPLE_LIMIT = 5


def same_origin_location(value: str, base_url: str) -> str | None:
    value = value.strip()
    if not value.lower().startswith(("https://", "http://")) or len(value) > 2048:
        return None
    try:
        url = normalize_site_url(value)
    except (ValueError, InternalAdapterError):
        return None
    return url if site_origin(url) == site_origin(base_url) else None


def parse_site_resource(page: FetchedSiteResource, kind: SiteResourceKind) -> SiteResourceReport:
    status = page.status_code
    report = SiteResourceReport(
        resource_type=kind,
        requested_url=page.requested_url,
        final_url=page.final_url,
        status_code=status,
        status="parsed",
        source=DataSource.PAGE_HTML,
        is_estimated=False,
    )
    if status in {404, 410}:
        report.status = "not_found"
        return report
    if not 200 <= status < 300:
        report.status = "http_error"
        report.integrity = DataIntegrity.PARTIAL
        return report
    if kind == "robots":
        for line in page.text.splitlines():
            field, separator, value = line.split("#", 1)[0].partition(":")
            if not separator:
                continue
            field, value = field.strip().lower(), value.strip()
            if field in {"user-agent", "allow", "disallow"} and len(report.rules) < SAMPLE_LIMIT:
                report.rules.append(f"{field}: {value[:300]}")
            if field == "sitemap" and len(report.declared_sitemaps) < SAMPLE_LIMIT:
                url = same_origin_location(value, page.final_url)
                if url and url not in report.declared_sitemaps:
                    report.declared_sitemaps.append(url)
        return report

    # Sitemaps contain URL data only. Keep DTDs and entity definitions out of the parser.
    try:
        if "<!DOCTYPE" in page.text:
            raise ElementTree.ParseError("DOCTYPE is outside the sitemap format")
        root = ElementTree.fromstring(page.text)
        namespace, _, local_name = root.tag.rpartition("}")
        prefix = namespace + "}" if namespace else ""
        if local_name not in {"urlset", "sitemapindex"}:
            raise ElementTree.ParseError("unexpected sitemap root")
    except ElementTree.ParseError:
        report.status = "invalid"
        report.integrity = DataIntegrity.PARTIAL
        report.message = "站点地图不是可解析的 urlset 或 sitemapindex XML。"
        return report

    report.document_type = local_name
    entry_tag = prefix + ("url" if local_name == "urlset" else "sitemap")
    entries = root.findall(entry_tag)
    report.entry_count = len(entries)
    for entry in entries:
        value = (entry.findtext(prefix + "loc") or "").strip()
        url = same_origin_location(value, page.final_url)
        if url is None:
            report.excluded_entry_count += 1
        elif len(report.sample_urls) < SAMPLE_LIMIT and url not in report.sample_urls:
            report.sample_urls.append(url)
    return report
