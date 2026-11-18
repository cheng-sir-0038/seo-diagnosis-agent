from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from src.agent.evals.trajectory import TrajectoryCase, evaluate_agent_run
from src.agent.models import AgentEvent, AgentRun, RunStatus

CASE_FILE = Path(__file__).resolve().parents[3] / "tests/agent_evals/seo_chat_cases.json"
CHAT_PATH = "/api/v1/seo-agent/chat"
STOPPED = {"COMPLETED", "FAILED", "CANCELLED", "WAITING_FOR_INPUT", "RECOVERY_BLOCKED"}
UPSTREAM_ERRORS = {
    "AUTH_FAILED", "CONFIG_ERROR", "DATA_NOT_AVAILABLE", "RATE_LIMITED",
    "TOOL_TIMEOUT", "UPSTREAM_ERROR", "UPSTREAM_TIMEOUT",
}


def _read_fact(data: Any, path: list[str | int]) -> str:
    try:
        for key in path:
            data = data[key]
    except (IndexError, KeyError, TypeError):
        return ""
    return " ".join(data.split()) if isinstance(data, str) else ""


def score_run(
    case: TrajectoryCase, detail: dict[str, Any], task: dict[str, Any], elapsed_ms: int
) -> dict[str, Any]:
    events = [
        AgentEvent(
            event_id=row["id"], run_id=detail["id"], type=row["event_type"],
            sequence=row["sequence"], created_at=row["created_at"], payload=row["payload"],
        )
        for row in detail["events"]
    ]
    status = RunStatus(detail["status"].lower())
    available = set(task["available_tools"])
    evaluation = evaluate_agent_run(
        case,
        AgentRun(
            id=detail["id"], messages=[{"role": "user", "content": task["user_intent"]}],
            status=status, events=events,
            partial=any(e.type == "run.completed" and e.payload.get("partial") for e in events),
        ),
        available_tools=available,
    )
    missing_tools = sorted(set(case.required_tools) - available)
    blocked = [f"未注册工具: {name}" for name in missing_tools]
    if status == RunStatus.RECOVERY_BLOCKED:
        blocked.append("运行恢复受阻")
    evidence = []
    completed = {}
    failed = set()
    answer = ""
    for event in events:
        payload = event.payload
        if event.type == "message.completed":
            answer = " ".join(str(payload.get("content") or "").split())
        if event.type == "tool.failed":
            name, code = payload.get("name"), payload.get("error_code")
            failed.add(name)
            if name in case.required_tools and code in UPSTREAM_ERRORS:
                blocked.append(f"{name}: {code}")
        if event.type == "tool.completed":
            name = payload["name"]
            data = (payload.get("result") or {}).get("data") or {}
            completed[name] = data
            source = (data.get("provenance") or {}).get("source")
            evidence.append({
                "tool": name, "source": source, "latency_ms": payload.get("latency_ms"),
                "coverage": {
                    step: item.get("status") for step, item in (data.get("coverage") or {}).items()
                },
            })

    for name in sorted(set(case.required_tools).intersection(evaluation.tools)):
        if name not in completed and name not in failed:
            evaluation.failures.append(f"必需工具没有返回结果: {name}")

    facts = []
    for check in case.answer_facts:
        if check.tool not in completed:
            continue
        expected = _read_fact(completed[check.tool], check.path)
        matched = bool(expected and expected in answer)
        path = "/".join(map(str, check.path))
        facts.append({"tool": check.tool, "path": path, "matched": matched})
        if not expected:
            blocked.append(f"缺少可核实字段: {check.tool}/{path}")
        elif not matched:
            evaluation.failures.append(f"回复未准确引用: {check.tool}/{path}")

    return {
        "case_id": case.case_id,
        "status": "BLOCKED" if blocked else "FAIL" if evaluation.failures else "PASS",
        "run_id": detail["id"],
        "model": detail.get("model_name"),
        "prompt_hash": task.get("prompt_hash"),
        "terminal_status": status.value,
        "tools": evaluation.tools,
        "evidence": evidence,
        "answer_fact_checks": facts,
        "failures": evaluation.failures,
        "blocked": blocked,
        "elapsed_ms": elapsed_ms,
        "chat_loop_token_usage": detail.get("token_usage", {}),
    }


def run_case(client: httpx.Client, site_id: str, case: TrajectoryCase, timeout: float) -> dict:
    started = time.monotonic()
    response = client.post(
        f"{CHAT_PATH}/runs",
        json={
            "site_id": site_id, "conversation_id": f"seo-eval-{uuid4().hex}",
            "messages": [{"role": "user", "content": case.user_input}],
        },
    )
    created = response.raise_for_status().json()
    run_id = created["run_id"]
    while time.monotonic() - started < timeout:
        detail = client.get(f"{CHAT_PATH}/runs/{run_id}").raise_for_status().json()
        status = detail["status"]
        if status == "RECOVERY_BLOCKED" or (
            status in STOPPED and any(
                e["event_type"] == f"run.{status.lower()}" for e in detail["events"]
            )
        ):
            task = client.get(f"{CHAT_PATH}/tasks/{created['task_id']}").raise_for_status().json()
            return score_run(case, detail, task, round((time.monotonic() - started) * 1000))
        time.sleep(0.5)
    return {"case_id": case.case_id, "status": "BLOCKED", "run_id": run_id,
            "blocked": ["运行在等待时间内未到达结束或待人工确认状态"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="用运行中的 SEO Agent 执行固定问题并核对运行记录")
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--case", action="append", help="只运行指定 case_id，可重复使用")
    parser.add_argument("--timeout", type=float, default=100, help="每题的等待时间，单位秒")
    args = parser.parse_args()
    cases = [TrajectoryCase.model_validate(item) for item in json.loads(CASE_FILE.read_text())]
    if args.case:
        unknown = set(args.case) - {case.case_id for case in cases}
        if unknown:
            parser.error(f"未知用例: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case.case_id in args.case]
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")

    results = []
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        for case in cases:
            try:
                results.append(run_case(client, args.site_id, case, args.timeout))
            except httpx.HTTPError as exc:
                reason = (
                    f"HTTP {exc.response.status_code}"
                    if isinstance(exc, httpx.HTTPStatusError)
                    else type(exc).__name__
                )
                results.append({"case_id": case.case_id, "status": "BLOCKED", "blocked": [reason]})
            print(f"{case.case_id} {results[-1]['status']}", file=sys.stderr, flush=True)
    counts = Counter(result["status"] for result in results)
    print(json.dumps({"results": results, "counts": counts}, ensure_ascii=False, indent=2))
    return int(bool(counts["FAIL"] or counts["BLOCKED"]))


if __name__ == "__main__":
    raise SystemExit(main())
