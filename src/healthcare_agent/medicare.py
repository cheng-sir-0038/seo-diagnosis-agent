from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from itertools import chain
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from src.agent.tools.base import ToolResult, ToolRuntimeContext
from src.healthcare_agent.results import HealthcareTool, RuleStatus

_SOURCE = "国家医疗保障局"
_SECTION = "常规目录西药部分"
_PDF_URL = "https://www.nhsa.gov.cn/module/download/downfile.jsp?classid=0&filename="
_ROW = re.compile(r"^[ \t]*([甲乙])[ \t]+(★\(\d+\)|\d+)[ \t]*(\S[^\r\n]*)", re.M)
_FORM = re.compile(r"[\w（）()、/－-]{1,24}(?:剂型|制剂|剂|片|胶囊|贴|剂盒|粉|丸|散|膏|栓|液|颗粒)")
_PDF_LIMIT = 10_000_000
_MAX_MATCHES = 8


@dataclass(frozen=True)
class Catalogue:
    year: int
    from_date: date
    until_date: date | None
    attachment: str
    notice: str
    last_western_page: int

    @property
    def url(self) -> str:
        return _PDF_URL + self.attachment


_CATALOGUES = {
    2025: Catalogue(
        2025, date(2026, 1, 1), date(2027, 1, 1),
        "a32f9f2f3fc046afaf08471f87456ce3.pdf",
        "https://www.nhsa.gov.cn/art/2025/12/7/art_53_18971.html", 82,
    ),
    2026: Catalogue(
        2026, date(2027, 1, 1), None,
        "95cee7b0c6544c0390fd4263456b5afd.pdf",
        "https://www.nhsa.gov.cn/art/2026/11/14/art_104_23384.html", 81,
    ),
}


def _name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


@dataclass(frozen=True)
class DrugRow:
    name: str
    mark: str
    number: str
    form: str
    page: int


def _rows_on_page(page_number: int, page: str) -> Iterator[DrugRow]:
    for match in _ROW.finditer(page):
        mark, number, remainder = match.groups()
        name, *remaining = re.split(r" {2,}", remainder.strip())
        form = next(iter(remaining), "")
        yield DrugRow(name, mark, number, form if _FORM.fullmatch(form) else "", page_number)


def _read_western_rows(content: bytes, catalogue: Catalogue) -> tuple[DrugRow, ...]:
    reader = PdfReader(BytesIO(content))
    if len(reader.pages) <= catalogue.last_western_page:
        raise ValueError("目录 PDF 页数不符合预期")
    pages = [
        reader.pages[index].extract_text(extraction_mode="layout")
        for index in range(8, catalogue.last_western_page + 1)
    ]
    if "西药部分" not in pages[0] or "中成药部分" not in pages[-1]:
        raise ValueError("目录 PDF 分节发生变化")
    rows = tuple(chain.from_iterable(
        _rows_on_page(number, page) for number, page in enumerate(pages[:-1], 9)
    ))
    if len(rows) < 1000:
        raise ValueError("西药表未完整读取")
    return rows


_cached_rows: dict[int, tuple[DrugRow, ...]] = {}
_catalogue_lock = asyncio.Lock()


async def _catalogue_rows(catalogue: Catalogue) -> tuple[DrugRow, ...]:
    async with _catalogue_lock:
        if catalogue.year not in _cached_rows:
            async with httpx.AsyncClient(timeout=8.0) as client:
                async with client.stream("GET", catalogue.url) as reply:
                    reply.raise_for_status()
                    content = bytearray()
                    async for chunk in reply.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > _PDF_LIMIT:
                            raise ValueError("目录 PDF 大小超出限制")
            if not content.startswith(b"%PDF-"):
                raise ValueError("公开附件未返回 PDF")
            _cached_rows[catalogue.year] = await asyncio.to_thread(
                _read_western_rows, bytes(content), catalogue,
            )
        return _cached_rows[catalogue.year]


class MedicareQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    drug_name: str = Field(min_length=1, max_length=80, description="药品通用名称")
    catalogue_year: Literal[2025, 2026] | None = Field(
        default=None, description="用户明确要求的目录版次。省略时使用当前生效版",
    )


class MedicareDrugCatalogue(HealthcareTool):
    name = "medicare_drug_catalogue"
    description = "查国家医保局常规西药目录的通用名、甲乙分类、目录剂型和原文页码。"
    parameters_schema = MedicareQuery.model_json_schema()
    timeout_seconds = 32.0

    async def execute(
        self, arguments: dict, runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = MedicareQuery.model_validate(arguments)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if options.catalogue_year:
            catalogue = _CATALOGUES[options.catalogue_year]
        else:
            catalogue = next(
                (c for c in reversed(_CATALOGUES.values()) if c.from_date <= today), None,
            )
            # Only the two pinned publications have been verified.
            if today.year > max(_CATALOGUES) + 1:
                catalogue = None
        if catalogue is None:
            return ToolResult(
                success=False, error_code="CATALOGUE_EDITION_UNAVAILABLE",
                error="现有来源无法确认当前生效版，请明确要查 2025 年版还是 2026 年版。",
            )
        try:
            rows = await _catalogue_rows(catalogue)
        except (httpx.HTTPError, ValueError) as exc:
            return ToolResult(
                success=False, error_code="CATALOGUE_UNAVAILABLE",
                error=f"本次未取得可核对的医保目录 PDF（{type(exc).__name__}）。",
            )

        name = _name(options.drug_name)
        matches = [row for row in rows if _name(row.name) == name]
        effective = f"{catalogue.year} 年版，自 {catalogue.from_date.isoformat()} 起执行"
        if today < catalogue.from_date:
            effective += "（尚未生效）"
        elif catalogue.until_date and today >= catalogue.until_date:
            effective += "（已被新版替换）"
        items = [
            {
                "key": f"{row.number}-{row.page}-{index}",
                "title": row.name,
                "summary": row.form or "剂型以目录原文为准",
                "url": f"{catalogue.url}#page={row.page}",
                "metadata": [
                    {"label": "目录分类", "value": f"{row.mark}类"},
                    {"label": "目录编号", "value": row.number},
                    {"label": "PDF 页码", "value": str(row.page)},
                ],
            }
            for index, row in enumerate(matches[:_MAX_MATCHES])
        ]
        items.append({
            "key": "notice", "title": "国家医保局发布通知",
            "summary": effective, "url": catalogue.notice,
        })
        if matches:
            summary = f"{effective}。在{_SECTION}找到 {len(matches)} 条同名记录。"
        else:
            summary = f"{effective}。在{_SECTION}没有定位到这一通用名的同名条目。"
        return self.cards(
            "国家医保药品目录", _SOURCE, summary, items,
            status=RuleStatus.MATCHED if matches else RuleStatus.NO_RECORD,
            answer=(
                f"{summary} 目录条目和对应原文可在下方核对。"
                "实际支付条件请核对目录备注和参保地规则。"
            ),
        )
