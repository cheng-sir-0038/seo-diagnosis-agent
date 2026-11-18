from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

FindingCode = Literal[
    "TITLE_MISSING", "TITLE_MULTIPLE", "DESCRIPTION_MISSING", "DESCRIPTION_MULTIPLE",
    "H1_MISSING", "H1_MULTIPLE", "CANONICAL_MULTIPLE", "NOINDEX_DECLARED",
]

_LABELS = {
    "title_count": "Title 数量",
    "meta_description_count": "描述数量",
    "h1_count": "H1 数量",
    "canonical_count": "Canonical 数量",
    "canonical_url": "首个 Canonical",
    "canonical_hrefs": "Canonical 声明样例",
    "robots": "Robots 标签",
    "googlebot": "Googlebot 标签",
    "x_robots_tag": "X-Robots-Tag 响应头",
}


class PageFinding(BaseModel):
    code: FindingCode
    status: Literal["observed", "needs_review"]
    url: str
    title: str
    reason: str
    observed: dict[str, int | str | list[str] | None]
    source: Literal["page_html"] = "page_html"
    fetched_at: str | None = None
    cached: bool = False

    def card(self) -> dict[str, Any]:
        fact = "已观察" if self.status == "observed" else "待核实"
        source = "页面响应头及源 HTML" if self.code == "NOINDEX_DECLARED" else "页面源 HTML"
        observations = []
        for key, value in self.observed.items():
            text = " · ".join(value) if isinstance(value, list) else value
            observations.append({
                "label": _LABELS[key], "value": str(text) if text is not None else "无",
            })
        return {
            "key": f"finding-{self.code}",
            "title": self.title,
            "url": self.url,
            "summary": self.reason,
            "metadata": [
                {"label": "判断状态", "value": fact},
                {"label": "受检页面", "value": self.url},
                *observations,
                {"label": "数据来源", "value": source},
                *([{"label": "读取方式", "value": "缓存数据"}] if self.cached else []),
            ],
        }


def page_findings(page: dict[str, Any], *, cached: bool) -> list[PageFinding]:
    url, status = page.get("final_url"), page.get("status_code")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return []
    if type(status) is not int or not 200 <= status < 300:
        return []

    findings: list[PageFinding] = []

    def add(
        code: FindingCode, title: str, reason: str,
        observed: dict[str, int | str | list[str] | None], *, review: bool = True,
    ) -> None:
        findings.append(PageFinding(
            code=code, status="needs_review" if review else "observed",
            url=url, title=title, reason=reason, observed=observed,
            fetched_at=page.get("fetched_at"), cached=cached,
        ))

    for field, label, missing, multiple in (
        ("title", "Title", "TITLE_MISSING", "TITLE_MULTIPLE"),
        ("meta_description", "Meta Description", "DESCRIPTION_MISSING", "DESCRIPTION_MULTIPLE"),
        ("h1", "H1", "H1_MISSING", "H1_MULTIPLE"),
    ):
        count_key = f"{field}_count"
        count = page.get(count_key)
        if type(count) is not int or count < 0:
            continue
        values = {count_key: count}
        if count == 0 or (field in page and not page[field]):
            add(
                missing, f"源 HTML 缺少 {label}", f"在本次读取的源 HTML 中未找到非空 {label}。",
                values, review=field != "title",
            )
        if count > 1:
            add(multiple, f"发现多个 {label}", "请结合页面用途核对这些声明。", values)

    canonical_count = page.get("canonical_count")
    if type(canonical_count) is int and canonical_count > 1:
        addresses = page.get("canonical_hrefs")
        observed: dict[str, int | str | list[str] | None] = {"canonical_count": canonical_count}
        if addresses:
            observed["canonical_hrefs"] = addresses
        else:
            observed["canonical_url"] = page.get("canonical_url")
        add(
            "CANONICAL_MULTIPLE", "发现多个 Canonical 声明",
            "源 HTML 中存在多个 Canonical，需要核对哪一个是站点希望使用的地址。",
            observed,
        )
    if page.get("declared_noindex") is True:
        fields = ("robots", "googlebot", "x_robots_tag")
        add(
            "NOINDEX_DECLARED", "页面声明了 noindex",
            "已观察到声明。请核对页面用途及适用的爬虫范围，实际收录状态未知。",
            {field: page[field] for field in fields if page.get(field)},
        )
    return findings
