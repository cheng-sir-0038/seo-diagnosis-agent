from __future__ import annotations

import asyncio
import re
import time

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.agent.tools.base import ToolResult, ToolRuntimeContext
from src.healthcare_agent.results import HealthcareTool, RuleStatus

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_PUBMED = "https://pubmed.ncbi.nlm.nih.gov"
_no_key_lock = asyncio.Lock()
_last_request = 0.0


class LiteratureQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    query: str = Field(min_length=2, max_length=240, description="公开文献检索词，尽量使用英文")
    from_year: int | None = Field(default=None, ge=1900, le=2100, description="发文年份下限")
    until_year: int | None = Field(default=None, ge=1900, le=2100, description="发文年份上限")
    limit: int = Field(default=3, ge=1, le=5)


def _literature_item(pmid: str, row: dict, options: LiteratureQuery) -> dict | None:
    title = row.get("title")
    if not title:
        return None
    online, printed = row.get("epubdate"), row.get("pubdate")
    if options.from_year or options.until_year:
        first = re.match(r"\d{4}", online or printed or "")
        if first is None or not (
            (options.from_year or 1900) <= int(first[0]) <= (options.until_year or 2100)
        ):
            return None
    metadata = [
        {"label": "PMID", "value": pmid},
        {"label": "期刊日期", "value": printed or "未提供"},
        {"label": "文献类型", "value": " / ".join(row.get("pubtype") or []) or "未提供"},
    ]
    if online and online != printed:
        metadata.insert(1, {"label": "在线发表", "value": online})
    return {
        "key": pmid, "title": title, "url": f"{_PUBMED}/{pmid}/",
        "summary": row.get("fulljournalname") or "", "metadata": metadata,
    }


async def _eutils(client: httpx.AsyncClient, endpoint: str, **params: str | int) -> dict:
    global _last_request
    async with _no_key_lock:
        wait = (1 / 3) - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()
        response = await client.get(
            f"{_EUTILS}/{endpoint}.fcgi", params={"db": "pubmed", "retmode": "json", **params},
        )
        response.raise_for_status()
        return response.json()


class PubMedLiteratureSearch(HealthcareTool):
    name = "pubmed_literature_search"
    description = "实时检索公开 PubMed 题录，返回 PMID、期刊、发表日期、文献类型和原文入口。"
    parameters_schema = LiteratureQuery.model_json_schema()

    async def execute(
        self, arguments: dict, runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = LiteratureQuery.model_validate(arguments)
        if options.from_year and options.until_year and options.from_year > options.until_year:
            return ToolResult(
                success=False, error_code="INVALID_YEAR_RANGE", error="起始年份晚于结束年份。",
            )
        params: dict[str, int | str] = {"term": options.query, "retmax": options.limit}
        if options.from_year or options.until_year:
            params.update(
                datetype="pdat",
                mindate=options.from_year or 1900, maxdate=options.until_year or 2100,
            )
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                search = (await _eutils(client, "esearch", **params))["esearchresult"]
                total = int(search["count"])
                ids = [key for key in search["idlist"] if isinstance(key, str) and key.isdigit()]
                if total and not ids:
                    raise ValueError("PubMed 未返回可核对的 PMID")
                records = (
                    (await _eutils(client, "esummary", id=",".join(ids)))["result"] if ids else {}
                )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return ToolResult(
                success=False, error_code="PUBMED_UNAVAILABLE", error="本次未能取得 PubMed 题录。",
            )
        items = list(filter(None, (
            _literature_item(pmid, records.get(pmid, {}), options) for pmid in ids
        )))
        if items:
            summary = f"PubMed 检索命中 {total} 条，展示本次可核对的 {len(items)} 条公开题录。"
        elif total:
            summary = f"PubMed 检索命中 {total} 条，首批结果未取得符合所选日期的完整题录。"
        else:
            summary = "PubMed 未检索到匹配的题录。可以换一组关键词或放宽日期。"
        return self.cards(
            "PubMed 文献检索", "PubMed · NCBI", summary, items,
            status=RuleStatus.MATCHED if items else RuleStatus.NO_RECORD, query=options.query,
            hit_count=total, answer=summary,
        )
