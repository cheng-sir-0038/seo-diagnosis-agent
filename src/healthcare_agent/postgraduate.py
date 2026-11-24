from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Index, func, or_, select
from sqlalchemy.dialects.postgresql import TSVECTOR, insert
from sqlalchemy.orm import Mapped, mapped_column

from src.agent.tools.base import ToolResult, ToolRuntimeContext
from src.core.text_search import normalize_search_text as _normalize
from src.core.text_search import search_tokens as _tokens
from src.healthcare_agent.postgraduate_sources import EXCERPTS, PROGRAM
from src.healthcare_agent.results import HealthcareTool, ask, resumed_arguments
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import Base

POSTGRADUATE_TOOL = "postgraduate_admissions_search"
DEFAULT_TOP_K = 3
MAX_TOP_K = 6
MAX_QUERY_LENGTH = 320


class RetrievalStatus(StrEnum):
    HIT = "hit"
    PARTIAL = "partial"
    NO_HIT = "no_hit"
    NEEDS_INPUT = "needs_input"


class PostgraduateChunk(Base):
    __tablename__ = "postgraduate_admission_chunks"
    __table_args__ = (
        Index("ix_postgraduate_scope", "school", "year", "program"),
        Index("ix_postgraduate_search", "search_vector", postgresql_using="gin"),
    )

    chunk_id: Mapped[str] = mapped_column(primary_key=True)
    school: Mapped[str]
    year: Mapped[int]
    program: Mapped[str]
    title: Mapped[str]
    source_url: Mapped[str]
    section: Mapped[str]
    topic: Mapped[str]
    quote: Mapped[str]
    search_vector: Mapped[str] = mapped_column(TSVECTOR)


_SCHOOLS = {"武科大": "武汉科技大学", "湖北民大": "湖北民族大学"}
_PROGRAMS = dict.fromkeys(
    (
        "临床医学专硕",
        "临床医学专业学位",
        "临床医学专业硕士",
        "临床医学硕士专业学位",
        "临床医学专业学位硕士",
        "临床医学（专业学位）",
        "临床医学（专硕）",
        "1051",
    ),
    PROGRAM,
)
_REWRITE = {
    "考哪几门": "初试科目",
    "初试考什么": "初试科目",
    "复试考什么": "复试科目",
    "上岸率": "录取率",
    "考上的概率": "录取率",
    "录取概率": "录取率",
    "上岸概率": "录取率",
    "通过的概率": "通过率",
    "住培": "规培",
}
# A hit for "初试" alone cannot substantiate a tuition amount, rate or score.
_QUALIFIERS = (
    "录取率",
    "通过率",
    "报录比",
    "学费",
    "分数线",
    "奖学金",
    "住宿费",
    "报名人数",
    "报考人数",
    "录取人数",
    "招生人数",
)
_CATALOGUE_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_INTERNAL_MEDICINE = ("内科学专硕", "内科学专业学位", "内科学专业硕士")
_INTERNAL_MEDICINE_CODE = "105101"


async def seed_postgraduate_corpus(database: SeoAgentDatabase) -> None:
    """Index the versioned public excerpts before serving queries."""
    rows = []
    for item in EXCERPTS:
        terms = _tokens(item.topic + " " + item.quote)
        rows.append(
            {
                "chunk_id": item.chunk_id,
                "school": item.document.school,
                "year": item.document.year,
                "program": PROGRAM,
                "title": item.document.title,
                "source_url": item.document.url,
                "section": item.section,
                "topic": item.topic,
                "quote": item.quote,
                "search_vector": func.to_tsvector("simple", " ".join(sorted(terms))),
            }
        )
    statement = insert(PostgraduateChunk).values(rows)
    values = {key: getattr(statement.excluded, key) for key in rows[0] if key != "chunk_id"}
    await database.execute(
        statement.on_conflict_do_update(
            index_elements=[PostgraduateChunk.chunk_id],
            set_=values,
            where=or_(
                *(
                    getattr(PostgraduateChunk, key).is_distinct_from(value)
                    for key, value in values.items()
                )
            ),
        )
    )


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ProgrammeCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]


class AdmissionsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    query: str = Field(
        min_length=1, max_length=MAX_QUERY_LENGTH, description="用户的原始考研问题，保留查询指标"
    )
    schools: list[Name] = Field(
        default_factory=list, max_length=2, description="用户明确的 1–2 所院校"
    )
    year: int | None = Field(default=None, ge=1990, le=2200, description="用户给出的招生年份")
    program: Name | None = Field(
        default=None, description="用户报考的专业和学位类型，有六位目录代码时照录"
    )
    school_program_codes: dict[Name, ProgrammeCode] = Field(
        default_factory=dict, max_length=2,
        description="用户明确给出的院校和六位目录代码，如 武汉科技大学:105101",
    )
    k: int = Field(
        default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K, description="整个查询最多返回的段落数"
    )


def _reply(field: str, answer: str) -> object | None:
    if field == "year" and re.fullmatch(r"[12]\d{3}年?", answer.strip(" 。！")):
        return int(answer.strip()[:4])
    if field == "schools":
        names = [
            name.strip()
            for name in re.split(r"和|与|、|，|,|及", answer.strip(" 。！"))
            if name.strip()
        ]
        if 1 <= len(names) <= 2 and all(len(n) <= 80 for n in names):
            if all(n in _SCHOOLS or n.endswith(("大学", "学院")) for n in names):
                return names
    if field == "program":
        value = answer.strip(" 。！")
        if value in _PROGRAMS or value in _INTERNAL_MEDICINE or _CATALOGUE_CODE.fullmatch(value):
            return value
    return None


def _codes_by_school(options: AdmissionsQuery, schools: list[str], query: str) -> dict[str, str]:
    paired = {
        _SCHOOLS.get(school, school): code for school, code in options.school_program_codes.items()
        if _SCHOOLS.get(school, school) in schools
    }
    if len(schools) < 2:
        return paired
    aliases = {school: school for school in schools}
    aliases.update({alias: school for alias, school in _SCHOOLS.items() if school in schools})
    names = "|".join(re.escape(name) for name in sorted(aliases, key=len, reverse=True))
    # Prefer school/code pairs stated together in the user's actual question.
    for school, code in re.findall(rf"({names})[\s的:(／/-]*([0-9]{{6}})(?![0-9])", query):
        paired[aliases[school]] = code
    for code, school in re.findall(rf"(?<![0-9])([0-9]{{6}})\s*\(\s*({names})\s*\)", query):
        paired[aliases[school]] = code
    return paired


def _filters(options: AdmissionsQuery) -> dict:
    requested = _normalize(options.program or "")
    query = _normalize(options.query)
    schools = list(dict.fromkeys(_SCHOOLS.get(name, name) for name in options.schools))
    paired = _codes_by_school(options, schools, query)
    codes = {*_CATALOGUE_CODE.findall(requested + " " + query), *paired.values()}
    name = _CATALOGUE_CODE.sub("", requested).strip(" ()-")
    if (
        name in _INTERNAL_MEDICINE
        or (codes and name == "内科学")
        or any(exact in query for exact in _INTERNAL_MEDICINE)
        or re.search(r"专业名(?:称)?[为是：: ]*内科学|内科学专业(?!学术)", query)
    ):
        codes.add(_INTERNAL_MEDICINE_CODE)
    category = _PROGRAMS.get(options.program, options.program)
    if codes and all(code.startswith("1051") for code in codes):
        category = PROGRAM
    filters = {
        "schools": schools,
        "year": options.year,
        "program": category,
    }
    if codes:
        filters["program_codes"] = sorted(codes)
    if paired:
        filters["school_program_codes"] = paired
    return filters


def _receipt(
    options: AdmissionsQuery,
    runtime: ToolRuntimeContext | None,
    *,
    status: RetrievalStatus,
    hits: list[dict],
    missing_schools: list[str] | None = None,
) -> dict:
    receipt = {
        "run_id": runtime.run_id if runtime else None,
        "query": options.query,
        "filters": _filters(options),
        "k": options.k,
        "status": status.value,
        "hits": hits,
        "missing_schools": missing_schools or [],
    }
    fingerprint = json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode()
    receipt["receipt_id"] = "rag-" + hashlib.sha256(fingerprint).hexdigest()[:20]
    return receipt


class PostgraduateAdmissionsSearch(HealthcareTool):
    name = POSTGRADUATE_TOOL
    description = (
        "从已收录院校的官方考研资料按年份和专业检索，返回排名、原文和来源。比较两校时单次传入"
        "学校列表。分校指定六位目录代码时填 school_program_codes。询问初复试、规培资格及录取率时"
        "保留用户的查询目标。未知的院校、年份、专业留空。"
    )
    parameters_schema = AdmissionsQuery.model_json_schema()

    def __init__(self, database: SeoAgentDatabase):
        self.database = database

    async def execute(
        self,
        arguments: dict,
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        arguments, clarification = resumed_arguments(self.name, arguments, runtime, _reply)
        original = runtime.metadata.get("user_intent") if runtime else None
        if isinstance(original, str) and original.strip() and not runtime.metadata.get(
            "human_input_resume"
        ):
            arguments = {**arguments, "query": original.strip()[:MAX_QUERY_LENGTH]}
        options = AdmissionsQuery.model_validate(arguments)
        if clarification:
            clarification.data["receipt"] = _receipt(
                options, runtime, status=RetrievalStatus.NEEDS_INPUT, hits=[]
            )
            return clarification
        filters = _filters(options)
        for field, question in (
            ("schools", "你要查哪一所或哪两所学校？"),
            ("year", "请告诉我你要查哪一年的硕士招生资料。"),
            ("program", "你想报哪个专业、学硕还是专硕？"),
        ):
            value = filters[field]
            if not value or (field == "program" and value in {"临床医学", "临床", "内科学"}):
                pending = ask(field, question, options.model_dump(mode="json"))
                pending.data["receipt"] = _receipt(
                    options, runtime, status=RetrievalStatus.NEEDS_INPUT, hits=[]
                )
                return pending
        hits = await self._retrieve(options, filters)
        schools = filters["schools"]
        missing = [school for school in schools if all(hit["school"] != school for hit in hits)]
        status = RetrievalStatus.HIT
        if not hits:
            status = RetrievalStatus.NO_HIT
        elif missing:
            status = RetrievalStatus.PARTIAL
        receipt = _receipt(options, runtime, status=status, hits=hits, missing_schools=missing)
        items = [
            {
                "key": hit["chunk_id"],
                "title": f"{hit['school']} · {hit['section']}",
                "summary": hit["quote"],
                "url": hit["source_url"],
                "metadata": [
                    {"label": "招生年份", "value": str(hit["year"])},
                    {"label": "命中顺序", "value": str(hit["rank"])},
                    {"label": "片段编号", "value": hit["chunk_id"]},
                ],
            }
            for hit in hits
        ]
        if not items:
            items.append(
                {
                    "key": "no_hit",
                    "title": "已收录资料中没有匹配的依据",
                    "summary": "请核实院校、招生年份和专业，或查该校当年的招生公告。",
                }
            )
        summary = f"本次检索返回 {len(hits)} 个片段，上限 {options.k}。回执 {receipt['receipt_id']}"
        if hits and missing:
            summary += f"。未找到{'、'.join(missing)}的可引用片段。"
        return self.cards(
            "考研资料检索", "院校官方招生资料", summary, items,
            status=status.value, receipt=receipt, answer=render_admissions_answer(receipt),
        )

    async def _retrieve(self, options: AdmissionsQuery, filters: dict) -> list[dict]:
        query = _normalize(options.query)
        for metadata in (
            *filters["schools"],
            *_SCHOOLS,
            str(options.year),
            options.program or "",
            PROGRAM,
            "临床医学",
            "两所学校",
            "招生目录",
            "考研",
        ):
            if metadata:
                query = query.replace(_normalize(metadata), " ")
        for alias, wording in _REWRITE.items():
            query = query.replace(alias, wording)
        words = _tokens(query)
        if not words:
            return []
        tsquery = func.to_tsquery("simple", " | ".join(sorted(words)))
        vector = PostgraduateChunk.search_vector
        score = func.ts_rank_cd(vector, tsquery).label("score")
        conditions = [
            PostgraduateChunk.year == filters["year"],
            PostgraduateChunk.program == filters["program"],
            vector.bool_op("@@")(tsquery),
        ]
        required = [term for term in _QUALIFIERS if term in query]
        for term in required:
            conditions.append(vector.bool_op("@@")(func.plainto_tsquery("simple", term)))
        # With a specified exam phase, sharing only the word "科目" is insufficient.
        phases = [phase for phase in ("初试", "复试", "规培") if phase in query]
        if phases:
            conditions.append(vector.bool_op("@@")(func.to_tsquery("simple", " | ".join(phases))))
        results = []
        async with self.database.orm_session() as session:
            for order, school in enumerate(filters["schools"]):
                school_conditions = [*conditions, PostgraduateChunk.school == school]
                codes = filters.get("school_program_codes", {})
                scoped = [codes[school]] if school in codes else filters.get("program_codes", [])
                if scoped:
                    # Match this school's catalogue row, with OR for explicitly shared codes.
                    alternatives = "|".join(scoped)
                    school_conditions.append(PostgraduateChunk.section.regexp_match(
                        rf"(^|[^0-9])({alternatives})([^0-9]|$)"
                    ))
                statement = (
                    select(PostgraduateChunk, score)
                    .where(*school_conditions)
                    .order_by(score.desc(), PostgraduateChunk.chunk_id)
                    .limit(options.k)
                )
                for school_rank, (row, weight) in enumerate(await session.execute(statement)):
                    results.append((school_rank, -float(weight), order, row))
        # Each requested school gets its strongest passage first within the global top-k limit.
        results.sort(key=lambda hit: hit[:3])
        return [
            {
                "rank": rank,
                "chunk_id": row.chunk_id,
                "school": row.school,
                "year": row.year,
                "program": row.program,
                "title": row.title,
                "section": row.section,
                "quote": row.quote,
                "source_url": row.source_url,
                "score": round(-weight, 5),
            }
            for rank, (_, weight, _, row) in enumerate(results[: options.k], start=1)
        ]



def render_admissions_answer(receipt: dict) -> str:
    """Project the final answer from this run's exact evidence and coverage."""
    hits = receipt["hits"]
    if not hits:
        return (
            "在指定院校、年份和专业的已收录资料里，没有检索到支持这个问题的依据。"
            "可以继续核对该校当年的官方公告。"
        )
    excerpts = [
        f"**{h['school']}（{h['year']}）** · {h['section']}\n\n"
        f"“{h['quote']}” [{h['chunk_id']}]({h['source_url']})"
        for h in hits
    ]
    if missing := receipt["missing_schools"]:
        excerpts.append(f"本次回执没有返回{'、'.join(missing)}的可引用片段。")
    return "\n\n".join(excerpts)
