from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from src.agent.models import AgentRun, HumanInputState
from src.agent.providers.base import ToolCall
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.base import ToolResult
from src.procurement_agent.calculations import purchase_quantity
from src.procurement_agent.evidence import grounded_quote, grounded_record, item_key, safe_link
from src.procurement_agent.models import (
    INTAKE_PROMPT_KEY,
    MAX_LINES,
    MAX_QUOTES,
    MAX_RECORDS,
    Lookup,
    Need,
    Procedure,
    ProcurementIntake,
    RecordKind,
)
from src.procurement_agent.search_intent import requests_catalog_refresh
from src.procurement_agent.taobao import CATALOG_RESULT_LIMIT
from src.procurement_agent.tools import BUILD_LIST, CATALOG, COMPARE, RECONCILE
from src.seo_agent.workflows.structured_model import StructuredModelRunner

_INPUT_TOOL = "shopping_continue"
_END = {"结束", "结束本次整理", "结束采购整理"}
_LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_ITEM_HOSTS = {
    "item.taobao.com",
    "item.m.taobao.com",
    "detail.tmall.com",
    "detail.m.tmall.com",
    "h5.m.taobao.com",
}
_URL = re.compile(r"https?://[^\s<>，。；|\]）)]+")
_REPORT = {Procedure.COMPARE: COMPARE, Procedure.LIST: BUILD_LIST, Procedure.RECONCILE: RECONCILE}


class _ToolBudgetError(Exception):
    pass


def _named_id(text: str, name: str, value: str) -> bool:
    return bool(
        re.search(
            rf"{name}\s*(?:ID|编号)\s*[:：=]?\s*{re.escape(value)}(?![A-Za-z0-9_-])", text, re.I
        )
    )


def _lookup(text: str, extracted: Lookup | None) -> Lookup | None:
    parsed = []
    for raw in _URL.findall(text):
        url = safe_link(raw)
        if not url:
            continue
        parts = urlsplit(url)
        if parts.hostname in _ITEM_HOSTS:
            parsed.extend(parse_qs(parts.query).get("id", []))
    unique_ids = list(dict.fromkeys(parsed))
    explicit = bool(extracted and _named_id(text, "商品", extracted.item_id))
    candidate = extracted.item_id if extracted and explicit else None
    item_id = candidate or (unique_ids[0] if len(unique_ids) == 1 else None)
    if not item_id:
        return None
    coupon = None
    if (
        extracted
        and item_id == extracted.item_id
        and extracted.activity_id
        and _named_id(text, "(?:优惠券|券)", extracted.activity_id)
    ):
        coupon = extracted.activity_id
    try:
        return Lookup(item_id=item_id, activity_id=coupon)
    except ValidationError:
        return None


def _merge(saved: ProcurementIntake, updates: ProcurementIntake, text: str) -> ProcurementIntake:
    request = saved.model_dump()
    request.update(
        updates.model_dump(exclude_none=True, exclude={"needs", "quotes", "records", "lookup"})
    )
    demands = {item_key(need.item): need for need in saved.needs}
    for update in updates.needs:
        key = item_key(update.item)
        previous = demands.get(key)
        demands[key] = Need.model_validate(
            {
                **(previous.model_dump() if previous else {}),
                **update.model_dump(exclude_none=True),
            }
        )
    request["needs"] = list(demands.values())[:MAX_LINES]
    if lookup := _lookup(text, updates.lookup):
        request["lookup"] = lookup
    quotes = {(quote.supplier, quote.item, quote.evidence): quote for quote in saved.quotes}
    for offer in updates.quotes:
        quote = grounded_quote(offer, text)
        if quote is None:
            continue
        key = (quote.supplier, quote.item, quote.evidence)
        previous = quotes.get(key)
        if previous and quote.charges_for_quantity is None:
            quote = quote.model_copy(update={"charges_for_quantity": previous.charges_for_quantity})
        quotes[key] = quote
    records = {(row.reference, row.kind, row.evidence): row for row in saved.records}
    order_references = {row.reference for row in saved.records if row.kind == RecordKind.ORDER}
    order_references.update(
        row.reference
        for offer in updates.records
        if offer.kind == RecordKind.ORDER and (row := grounded_record(offer, text))
    )
    records.update(
        {
            (row.reference, row.kind, row.evidence): row
            for offer in updates.records
            if (row := grounded_record(offer, text, order_references))
        }
    )
    bound = []
    for quote in list(quotes.values())[-MAX_QUOTES:]:
        need = demands.get(item_key(quote.item))
        if need and quote.charges_for_quantity is None:
            scope = purchase_quantity(need, quote, request["people"])
            quote = quote.model_copy(update={"charges_for_quantity": scope})
        bound.append(quote)
    request.update(quotes=bound, records=list(records.values())[-MAX_RECORDS:])
    return ProcurementIntake.model_validate(request)


async def _tool(executor: AgentExecutor, run: AgentRun, name: str, args: dict) -> ToolResult:
    encoded = json.dumps(args, sort_keys=True, ensure_ascii=False).encode()
    if args.get("force_refresh") and run.human_input and run.human_input.answer:
        encoded += run.human_input.request_id.encode()
    call = ToolCall(f"{name}:{hashlib.sha256(encoded).hexdigest()[:20]}", name, args)
    if run.tool_count >= executor.max_tool_calls and not any(
        event.type == "tool.completed" and event.payload.get("tool_call_id") == call.id
        for event in run.events
    ):
        raise _ToolBudgetError
    return await executor.required_tool(run, call)


async def _wait(
    executor: AgentExecutor, run: AgentRun, question: str, text: str | None = None, *,
    force_refresh: bool = False,
) -> None:
    run.human_input = HumanInputState(
        request_id=f"input_{uuid4().hex}",
        tool_name=_INPUT_TOOL,
        tool_arguments={"force_refresh": True} if force_refresh else {},
        tool_result={},
        questions=[question],
        message=text,
    )
    await executor.run_turns(run)


async def _finish(executor: AgentExecutor, run: AgentRun, text: str) -> None:
    run.human_input = None
    await executor.run_turns(run, evidence_answer=lambda _: text)


def _catalog_arguments(intake: ProcurementIntake) -> dict | None:
    search_only = intake.procedure == Procedure.SEARCH
    if intake.lookup and (not search_only or not intake.needs):
        return {"lookup": intake.lookup.model_dump(mode="json", exclude_none=True)}
    with_quotes = set() if search_only else {item_key(quote.item) for quote in intake.quotes}
    keywords = [
        " ".join(filter(None, (need.item, need.specification)))[:120]
        for need in intake.needs
        if item_key(need.item) not in with_quotes
    ]
    return {"keywords": keywords, "tmall_only": bool(intake.tmall_only)} if keywords else None


async def _catalog(
    executor: AgentExecutor, run: AgentRun, intake: ProcurementIntake, *,
    show_results: bool = False, force_refresh: bool = False,
) -> list[dict]:
    arguments = _catalog_arguments(intake)
    if arguments is None:
        return []
    if show_results:
        arguments["show_results"] = True
    if force_refresh and arguments.get("keywords"):
        arguments["force_refresh"] = True
    result = await _tool(executor, run, CATALOG, arguments)
    if result.success:
        return result.data["sources"]
    return [
        {
            "source": "procurement",
            "status": "error",
            "message": "商品查询暂未完成，可以先用你提供的报价。",
        }
    ]


def _intake_question(procedure: Procedure, force_refresh: bool) -> str:
    if procedure == Procedure.RECONCILE:
        return "请贴订单号和手头已有的凭证原文，例如订单、收货、付款或退款记录。"
    if force_refresh:
        return "想刷新哪个商品？请发商品名称或规格。"
    if procedure == Procedure.SEARCH:
        return "想查什么商品？请发名称、型号或淘宝商品链接。"
    if procedure == Procedure.LIST:
        return "这次要准备哪些物品？请列出名称，已知的数量或活动人数也可以一并告诉我。"
    return "想比较什么商品的报价？请发商品名称，已有的规格、数量或商家报价也可以一起贴。"


def _comparison_question(report: dict) -> str:
    lines, quotes = report["lines"], report["quotes"]
    for line in lines:
        missing = [
            label for field, label in (
                ("specification", "目标规格（如型号、容量或尺寸）"),
                ("quantity", "所需数量"),
                ("unit", "计量单位"),
            ) if line[field] is None
        ]
        if missing:
            return f"请补充「{line['item']}」的{'、'.join(missing)}。"
    for line in lines:
        if line["quantity_estimated"]:
            estimated = f"{line['quantity']} {line['unit']}"
            return f"「{line['item']}」暂按 {estimated}估算，请回复最终需要的数量。"
    for line in lines:
        if not any(row["item"] == line["item"] for row in quotes):
            return f"可贴「{line['item']}」的商家报价原文，最好包含每包数量、起订量、含税价和运费。"
    for row in quotes:
        if row["issues"]:
            subject = f"「{row['supplier']}」的「{row['item']}」"
            return f"可补充{subject}报价原文。{row['issues'][0]}。"
    if not lines:
        return "请补充该商品的目标规格、数量和计量单位，已有的商家报价也可以一并贴过来。"
    return "可贴其他商家的报价，或告诉我你想调整的规格、数量或到货条件。"


def _comparison_summary(report: dict, procedure: Procedure) -> str:
    lines, quotes = report["lines"], report["quotes"]
    if not quotes:
        return "已整理采购需求。需要商家报价才能核算实买数量与费用。"
    if any(line["quantity_estimated"] for line in lines):
        return "清单含按人数估算的用量。请先确认实际需求数量，费用可作为询价参考。"

    is_list = procedure == Procedure.LIST
    if not is_list and len(lines) > 1:
        return "已按物品拆开比价。各项报价的费用和交付条件可在表中逐项核对。"
    options = report["plans"] if is_list else quotes
    if not any(option["total_cny"] is not None for option in options):
        if is_list and not options:
            return "目前还没有覆盖全部物品的可核算采购组合，请逐项核对报价和交付条件。"
        return "现有报价还算不出完整总费用，卡片已列出各项已知费用和待确认条款。"

    budget_requested = report["budget_cny"] is not None
    deadline_requested = bool(report["needed_by"])
    for index, option in enumerate(options, start=1):
        if option["total_cny"] is None:
            continue
        if budget_requested and option["budget_status"] != "within":
            continue
        if option.get("delivery") in {"unconfirmed", "late"} or option.get("delivery_gaps"):
            continue
        total = f"总费用 {option['total_cny']} 元"
        if budget_requested:
            total += "，在预算内"
        if deadline_requested:
            total += "，到货承诺符合期限"
        if is_list:
            return f"可优先核对采购组合 {index}，{total}。"
        quantity = f"实买 {option['actual_quantity']} {option['unit']}"
        return f"可优先核对「{option['supplier']}」的报价，{quantity}，{total}。"
    return "已有可核算总费用，目前没有完整方案满足已给出的预算或到货要求。"


async def run_procurement(
    executor: AgentExecutor,
    run: AgentRun,
    planner: StructuredModelRunner,
) -> None:
    pending = run.human_input
    if pending and pending.answer is None:
        await executor.run_turns(run)
        return
    saved = run.dispatch_state
    today = datetime.now(_LOCAL_ZONE).date().isoformat()
    text = next(
        (
            str(message["content"])
            for message in reversed(run.messages)
            if message["role"] == "user" and message.get("content")
        ),
        "",
    )
    message = pending.answer if pending and pending.answer else text
    force_refresh = requests_catalog_refresh(message) or bool(
        pending and pending.tool_arguments.get("force_refresh")
    )
    if "procurement" not in saved:
        extracted = ProcurementIntake.model_validate(saved.get("procurement_intake") or {})
        intake = _merge(ProcurementIntake(), extracted, text)
        saved["procurement"] = intake.model_dump(mode="json")
    else:
        intake = ProcurementIntake.model_validate(saved["procurement"])

    if pending and pending.answer:
        if not any(
            event.type == "input.accepted" and event.payload.get("request_id") == pending.request_id
            for event in run.events
        ):
            await executor.emit(
                run,
                "input.accepted",
                {
                    "request_id": pending.request_id,
                    "answer": pending.answer,
                    "action": pending.action,
                },
            )
        if pending.answer in _END:
            await _finish(executor, run, "本次采购整理已结束，已有结果仍可在对话里核对。")
            return
        if saved.get("procurement_input_id") != pending.request_id:
            try:
                result = await planner.invoke(
                    prompt_key=INTAKE_PROMPT_KEY,
                    schema=ProcurementIntake,
                    method="json_mode",
                    context={
                        "current_date": today,
                        "confirmed_request": intake.model_dump(
                            mode="json", exclude={"quotes", "records"}
                        ),
                        "user_message": pending.answer,
                    },
                    operation="workspace.procurement.intake",
                    business_object={"run_id": run.id},
                )
            except ValueError:
                await _wait(
                    executor, run, pending.questions[0],
                    "这次没有识别出可用于更新的采购信息，请再补充一次。",
                    force_refresh=force_refresh,
                )
                return
            for key, count in result.usage.items():
                run.token_usage[key] = run.token_usage.get(key, 0) + count
            intake = _merge(intake, result.output, pending.answer)
            saved["procurement"] = intake.model_dump(mode="json")
            saved["procurement_input_id"] = pending.request_id

    procedure = intake.procedure or Procedure.COMPARE
    has_input = (
        bool(intake.records) if procedure == Procedure.RECONCILE
        else bool(intake.needs or intake.lookup)
    )
    if not has_input:
        await _wait(
            executor, run, _intake_question(procedure, force_refresh), force_refresh=force_refresh,
        )
        return

    await executor.checkpoint(run, stage="procurement_conditions")
    try:
        if procedure == Procedure.SEARCH:
            sources = await _catalog(
                executor, run, intake, show_results=True, force_refresh=force_refresh,
            )
            count = min(
                CATALOG_RESULT_LIMIT, sum(len(source.get("items", [])) for source in sources)
            )
            lead = "已刷新查询，" if force_refresh else ""
            summary = (
                f"{lead}找到 {count} 条商品资料，卡片里可核对规格、包装和参考价。"
                if count else next(
                    (source["message"] for source in sources if source.get("message")),
                    "请告诉我想查的商品名称或规格。",
                )
            )
            await _finish(executor, run, summary)
            return
        sources = [] if procedure == Procedure.RECONCILE else await _catalog(
            executor, run, intake, force_refresh=force_refresh,
        )
        report = await _tool(
            executor,
            run,
            _REPORT[procedure],
            {
                "today": today,
                "intake": intake.model_dump(mode="json"),
                "sources": sources,
            },
        )
    except _ToolBudgetError:
        await _finish(executor, run, "本次整理已达到查询次数，已有结果保留在对话里。")
        return
    if not report.success:
        await _finish(executor, run, "本次资料未能整理，请核对数量、规格和费用后再试。")
        return

    if procedure == Procedure.RECONCILE:
        count = len(report.data["orders"])
        await _wait(
            executor,
            run,
            "可补充同一订单的付款、实收或退款资料，或回复「结束本次整理」。",
            f"已按订单核对 {count} 笔资料。卡片里可以查看数量差异和有凭证的支出。"
            "\n\n还有新凭证可以接着贴，完成后可回复「结束本次整理」。",
        )
        return
    await _wait(
        executor,
        run,
        _comparison_question(report.data),
        f"{_comparison_summary(report.data, procedure)}\n\n整理完毕可回复「结束本次整理」。",
    )
