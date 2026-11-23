from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.agent.models import AgentRun
from src.agent.tools.base import ToolRuntimeContext
from src.healthcare_agent import build_healthcare_registry
from src.healthcare_agent.postgraduate import (
    POSTGRADUATE_TOOL,
    PostgraduateAdmissionsSearch,
    PostgraduateChunk,
    RetrievalStatus,
    render_admissions_answer,
    seed_postgraduate_corpus,
)
from src.healthcare_agent.postgraduate_eval import evaluate
from src.healthcare_agent.skills import verified_healthcare_answer
from src.seo_agent.db.database import SeoAgentDatabase


@pytest.fixture
async def catalog(pg_url):
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    try:
        await seed_postgraduate_corpus(database)
        yield database
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_top_k_receipts_and_negative_cases(catalog):
    await seed_postgraduate_corpus(catalog)
    assert await catalog.fetch_value(select(func.count()).select_from(PostgraduateChunk)) == 9
    report = await evaluate(catalog)
    assert report["passed"] == report["case_count"] == 14
    assert report["hit_count"] == report["answerable_count"] == 6
    no_rate = next(case for case in report["cases"] if case["case"] == "no-rate")
    assert no_rate["pass"] and no_rate["hit"] is None and no_rate["receipt"]["hits"] == []
    compared = next(case["receipt"] for case in report["cases"] if case["case"] == "prelim-compare")
    assert [hit["rank"] for hit in compared["hits"]] == [1, 2]
    assert all(
        hit["source_url"].startswith("https://") and "306" in hit["quote"]
        for hit in compared["hits"]
    )
    assert len({hit["source_url"] for hit in compared["hits"]}) == 2


@pytest.mark.asyncio
async def test_resume_uses_supplied_year_and_preserves_query_identity(catalog):
    tool = build_healthcare_registry(database=catalog).get(POSTGRADUATE_TOOL)
    run = ToolRuntimeContext(run_id="run-example")
    args = {"schools": ["武科大"], "program": "临床医学专硕", "query": "初试科目", "k": 1}
    pending = await tool.execute(args, run)
    assert pending.data["receipt"]["status"] == RetrievalStatus.NEEDS_INPUT
    assert pending.metadata["human_input"]["questions"]
    resumed = run.model_copy(
        update={
            "metadata": {
                "human_input_resume": {
                    "tool_name": tool.name,
                    "tool_arguments": args,
                    "answer": "2025年",
                    "tool_result": pending.model_dump(mode="json"),
                }
            }
        }
    )
    result = await tool.execute(args, resumed)
    direct = await tool.execute({**args, "year": 2025}, run)
    receipt = result.data["receipt"]
    assert receipt == direct.data["receipt"]
    assert receipt["filters"] == {
        "schools": ["武汉科技大学"],
        "year": 2025,
        "program": "临床医学专硕",
    }
    assert (
        receipt["run_id"] == "run-example"
        and receipt["receipt_id"] != pending.data["receipt"]["receipt_id"]
    )
    assert [row["chunk_id"] for row in receipt["hits"]] == ["wust-2025-internal-prelim"]
    assert tool.resolve_render_hint(result.model_dump(mode="json")) == "cards"


@pytest.mark.asyncio
async def test_search_reads_new_indexed_passage_and_guards_generated_citations(catalog):
    # An independently supplied passage, deliberately outside the embedded corpus.
    text = "专业课包含森林测量。"
    url = "https://catalog.example/2026/forestry"
    async with catalog.orm_session(write=True) as session:
        session.add(
            PostgraduateChunk(
                chunk_id="fixture-2026-forestry-subjects",
                school="测试大学",
                year=2026,
                program="林学",
                title="测试目录",
                source_url=url,
                section="林学／专业课",
                topic="专业课",
                quote=text,
                search_vector=func.to_tsvector("simple", "森林 测量"),
            )
        )
    tool = PostgraduateAdmissionsSearch(catalog)
    run = AgentRun(id="test-run", messages=[])
    result = await tool.execute(
        {"schools": ["测试大学"], "year": 2026, "program": "林学", "query": "森林测量", "k": 1},
        ToolRuntimeContext(run_id=run.id),
    )
    receipt = result.data["receipt"]
    (hit,) = receipt["hits"]
    assert (hit["source_url"], hit["quote"]) == (url, text)
    assert verified_healthcare_answer(run, tool.name) is None
    run.emit("tool.completed", {"name": tool.name, "result": result.model_dump(mode="json")})
    answer = verified_healthcare_answer(run, tool.name)
    assert answer is not None and text in answer
    assert f"[fixture-2026-forestry-subjects]({url})" in answer
    no_match = await tool.execute(
        {"schools": ["测试大学"], "year": 2028, "program": "林学", "query": "录取率"},
        ToolRuntimeContext(run_id=run.id),
    )
    assert "没有检索到" in render_admissions_answer(no_match.data["receipt"])


@pytest.mark.asyncio
async def test_an_exact_catalogue_program_cannot_match_a_different_row(catalog):
    tool = PostgraduateAdmissionsSearch(catalog)
    for school, requested, question, expected in (
        ("湖北民族大学", "105101", "初试科目", []),
        ("武汉科技大学", "105100", "初试科目", []),
        ("湖北民族大学", "内科学专硕", "初试科目", []),
        ("湖北民族大学", "临床医学专硕", "请查专业代码105101的初试科目", []),
        ("湖北民族大学", None, "请查专业代码105100的初试科目", ["hbmzu-2026-clinical-prelim"]),
        ("武汉科技大学", "临床医学", "105101的初试科目", ["wust-2026-internal-prelim"]),
        ("武汉科技大学", "内科学专硕", "初试科目", ["wust-2026-internal-prelim"]),
        ("湖北民族大学", "105100", "初试科目", ["hbmzu-2026-clinical-prelim"]),
        ("武汉科技大学", "105101内科学", "初试科目", ["wust-2026-internal-prelim"]),
    ):
        result = await tool.execute(
            {"schools": [school], "program": requested, "query": question, "year": 2026}
        )
        receipt = result.data["receipt"]
        assert [hit["chunk_id"] for hit in receipt["hits"]] == expected, (school, requested)
        assert receipt["status"] == (RetrievalStatus.HIT if expected else RetrievalStatus.NO_HIT)


@pytest.mark.parametrize(("query", "expected"), [
    (
        "比较武汉科技大学 105101 和湖北民族大学 105100 的初试科目",
        {"wust-2026-internal-prelim", "hbmzu-2026-clinical-prelim"},
    ),
    ("比较武汉科技大学 105100 和湖北民族大学 105101 的初试科目", set()),
    (
        "湖北民族大学（105100）与武科大（105101）初试科目分别有哪些？",
        {"wust-2026-internal-prelim", "hbmzu-2026-clinical-prelim"},
    ),
])
@pytest.mark.asyncio
async def test_coded_comparison_respects_each_schools_program(catalog, query, expected):
    result = await PostgraduateAdmissionsSearch(catalog).execute({
        "schools": ["湖北民族大学", "武科大"],
        "year": 2026, "program": "临床医学专硕", "query": query,
    })
    receipt = result.data["receipt"]
    assert {hit["chunk_id"] for hit in receipt["hits"]} == expected
    if expected:
        assert receipt["status"] == RetrievalStatus.HIT
        assert receipt["filters"]["school_program_codes"] == {
            "湖北民族大学": "105100", "武汉科技大学": "105101",
        }


@pytest.mark.asyncio
async def test_valid_chunk_id_cannot_reverse_source_text(catalog):
    run = AgentRun(id="evidence-turn", messages=[])
    response = await PostgraduateAdmissionsSearch(catalog).execute(
        {"schools": ["武汉科技大学"], "year": 2026, "program": "105101", "query": "初试科目"},
        ToolRuntimeContext(run_id=run.id),
    )
    receipt = response.data["receipt"]
    run.emit("tool.completed", {"name": POSTGRADUATE_TOOL, "result": response.model_dump()})
    answer = verified_healthcare_answer(run, POSTGRADUATE_TOOL)
    assert answer and "不考英语一" not in answer
    assert receipt["hits"][0]["quote"] in answer


@pytest.mark.asyncio
async def test_partial_comparison_only_attributes_evidence_to_the_matching_school(catalog):
    result = await PostgraduateAdmissionsSearch(catalog).execute(
        {
            "schools": ["武汉科技大学", "湖北民族大学"],
            "program": "105101",
            "year": 2026,
            "query": "初试科目",
        }
    )
    receipt = result.data["receipt"]
    assert receipt["status"] == RetrievalStatus.PARTIAL
    assert receipt["missing_schools"] == ["湖北民族大学"]
    text = render_admissions_answer(receipt)
    assert "录取率很高" not in text
    assert "湖北民族大学也考" not in text
    assert "本次回执没有返回湖北民族大学的可引用片段" in text
    assert receipt["hits"][0]["quote"] in text

    complete = await PostgraduateAdmissionsSearch(catalog).execute(
        {
            "schools": ["武汉科技大学", "湖北民族大学"],
            "program": "临床医学专硕",
            "year": 2026,
            "query": "初试科目",
        }
    )
    evidence = complete.data["receipt"]
    assert evidence["status"] == RetrievalStatus.HIT
    answer = render_admissions_answer(evidence)
    assert all(hit["quote"] in answer for hit in evidence["hits"])
