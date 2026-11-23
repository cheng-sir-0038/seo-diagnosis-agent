"""Evaluate public admissions evidence retrieval against a fixed, labelled question set."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from src.agent.tools.base import ToolRuntimeContext
from src.healthcare_agent.postgraduate import (
    DEFAULT_TOP_K,
    PostgraduateAdmissionsSearch,
    RetrievalStatus,
    seed_postgraduate_corpus,
)
from src.seo_agent.db.database import SeoAgentDatabase

_WUST, _HBMZU = "武汉科技大学", "湖北民族大学"


@dataclass(frozen=True)
class EvalCase:
    key: str
    query: str
    schools: tuple[str, ...]
    expected_status: RetrievalStatus
    expected: tuple[str, ...] = ()
    year: int | None = 2026
    program: str | None = "临床医学专硕"


CASES = (
    EvalCase(
        "prelim-one",
        "初试科目有哪些？",
        (_WUST,),
        RetrievalStatus.HIT,
        ("wust-2026-internal-prelim",),
    ),
    EvalCase(
        "prelim-compare",
        "对比两校内科学初试考哪几门",
        (_WUST, _HBMZU),
        RetrievalStatus.HIT,
        ("wust-2026-internal-prelim", "hbmzu-2026-clinical-prelim"),
    ),
    EvalCase(
        "retest-compare",
        "两校复试科目分别是什么？",
        (_WUST, _HBMZU),
        RetrievalStatus.HIT,
        ("wust-2026-internal-retest", "hbmzu-2026-clinical-retest"),
    ),
    EvalCase(
        "previous-edition",
        "初试考什么",
        (_WUST, _HBMZU),
        RetrievalStatus.HIT,
        ("wust-2025-internal-prelim", "hbmzu-2025-clinical-prelim"),
        year=2025,
    ),
    EvalCase(
        "training",
        "我正在住培，是否满足报考资格？",
        (_HBMZU,),
        RetrievalStatus.HIT,
        ("hbmzu-2026-clinical-training",),
    ),
    EvalCase(
        "partial",
        "两校的复试科目是什么",
        (_WUST, "武汉大学"),
        RetrievalStatus.PARTIAL,
        ("wust-2026-internal-retest",),
    ),
    EvalCase("missing-school", "初试科目有哪些", (), RetrievalStatus.NEEDS_INPUT),
    EvalCase("missing-year", "初试考什么", (_WUST,), RetrievalStatus.NEEDS_INPUT, year=None),
    EvalCase(
        "missing-program", "初试查考试科目", (_HBMZU,), RetrievalStatus.NEEDS_INPUT, program=None
    ),
    EvalCase("future-edition", "初试科目是什么", (_WUST,), RetrievalStatus.NO_HIT, year=2028),
    EvalCase(
        "wrong-degree", "初试科目是什么", (_HBMZU,), RetrievalStatus.NO_HIT, program="临床医学学硕"
    ),
    EvalCase("unindexed-school", "初试科目", ("武汉大学",), RetrievalStatus.NO_HIT),
    EvalCase("no-rate", "报了初试的考生录取率有多少", (_WUST,), RetrievalStatus.NO_HIT),
    EvalCase("lexical-near-miss", "通过复试后有多少奖学金", (_HBMZU,), RetrievalStatus.NO_HIT),
)


async def evaluate(database: SeoAgentDatabase) -> dict:
    tool = PostgraduateAdmissionsSearch(database)
    results = []
    for item in CASES:
        response = await tool.execute(
            {
                "query": item.query,
                "schools": list(item.schools),
                "year": item.year,
                "program": item.program,
                "k": DEFAULT_TOP_K,
            },
            ToolRuntimeContext(run_id=f"eval-{item.key}"),
        )
        receipt = response.data["receipt"]
        actual = [hit["chunk_id"] for hit in receipt["hits"]]
        relevant = set(item.expected)
        hit_at_k = bool(relevant.intersection(actual)) if relevant else None
        wrong_scope = any(
            hit["school"] not in item.schools
            or hit["year"] != item.year
            or hit["program"] != item.program
            for hit in receipt["hits"]
        )
        passed = (
            item.expected_status == receipt["status"]
            and set(actual) == relevant
            and not wrong_scope
        )
        reasons = []
        if item.expected_status != receipt["status"]:
            reasons.append(f"期望 {item.expected_status}，实际 {receipt['status']}")
        if absent := sorted(relevant.difference(actual)):
            reasons.append("漏召回 " + ", ".join(absent))
        if extra := sorted(set(actual).difference(relevant)):
            reasons.append("误召回 " + ", ".join(extra))
        if wrong_scope:
            reasons.append("命中超出年份、院校或专业范围")
        results.append(
            {
                "case": item.key,
                "expected_status": item.expected_status,
                "expected_chunk_ids": list(item.expected),
                "actual_chunk_ids": actual,
                "hit": hit_at_k,
                "pass": passed,
                "reason": "；".join(reasons) or "状态和证据均符合预期",
                "receipt": receipt,
            }
        )
    passed = sum(item["pass"] for item in results)
    answered = [item for item in results if item["hit"] is not None]
    hits = sum(item["hit"] for item in answered)
    return {
        "dataset": "graduate-admissions-v1",
        "k": DEFAULT_TOP_K,
        "case_count": len(results),
        "passed": passed,
        "pass_rate": passed / len(results),
        "answerable_count": len(answered),
        "hit_count": hits,
        "hit_at_k": hits / len(answered),
        "scoring": {
            "pass_rate": "状态符合预期、实际片段集合与标注一致且范围正确的用例数 / 全部用例数",
            "hit_at_k": "前 K 条命中至少一个标注片段的可回答题数 / 有标注证据的可回答题数",
            "scope": "仅评估这组固定问题的检索回执，拒答与追问不计入命中率分母。",
        },
        "cases": results,
    }


async def _main(output: Path | None) -> bool:
    database = SeoAgentDatabase(os.environ.get("TEST_DATABASE_URL"))
    try:
        await database.startup()
        await seed_postgraduate_corpus(database)
        report = await evaluate(database)
    finally:
        await database.shutdown()
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        output.write_text(serialized + "\n", encoding="utf-8")
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in ("cases", "scoring")},
                ensure_ascii=False,
            )
        )
    else:
        print(serialized)
    return report["passed"] == report["case_count"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="逐题 JSON 回执输出文件，省略则输出到标准输出")
    raise SystemExit(0 if asyncio.run(_main(parser.parse_args().output)) else 1)
