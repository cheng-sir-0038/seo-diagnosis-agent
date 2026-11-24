from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from src.agent.config import Skill
from src.agent.tools.base import AgentTool, RenderHint, ToolResult, ToolRuntimeContext
from src.agent.tools.registry import ToolRegistry
from src.procurement_agent.calculations import compare, reconcile
from src.procurement_agent.models import DOMAIN, MAX_LINES, Lookup, Procedure, ProcurementIntake
from src.procurement_agent.taobao import TaobaoAffiliate

CATALOG = "shopping_catalog"
COMPARE = "shopping_compare_quotes"
BUILD_LIST = "shopping_build_list"
RECONCILE = "shopping_reconcile"

PROCUREMENT_SKILL = Skill(
    name="shopping-and-procurement",
    domain=DOMAIN,
    description="选品、比价、清单与到货核对",
    tools=[CATALOG, COMPARE, BUILD_LIST, RECONCILE],
    prompt_fragment=(
        "shopping_procurement 负责商品选购、纸质或粘贴的报价比较、活动采购清单、"
        "实到数量与采购支出核对。即使缺少规格、数量或链接也先分派。"
        "采购商品链接只作商品资料。如果用户明确在做商品页面SEO，仍分给seo。"
    ),
)


class _CatalogQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list, max_length=MAX_LINES)
    tmall_only: bool = False
    lookup: Lookup | None = None


class _ReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    today: date
    intake: ProcurementIntake
    sources: list[dict] = Field(default_factory=list, max_length=MAX_LINES)


class _Tool(AgentTool):
    def __init__(
        self,
        name: str,
        description: str,
        query: type[BaseModel],
        handler: Callable[..., Awaitable[dict]],
        *,
        render_hint: RenderHint | None = None,
    ) -> None:
        self.name, self.description, self.query = name, description, query
        self.parameters_schema = query.model_json_schema()
        self.handler, self.render_hint = handler, render_hint

    async def execute(
        self,
        arguments: dict,
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        report = await self.handler(self.query.model_validate(arguments))
        return ToolResult(success=True, data=report)


def _yuan(value) -> str | None:
    return f"{value} 元" if value is not None else None


def _card(
    key: str,
    title: str,
    fields: dict,
    summary: str | None = None,
    url: str | None = None,
) -> dict:
    return {
        "key": key,
        "title": title,
        "summary": summary,
        "url": url,
        "metadata": [
            {"label": label, "value": str(value)}
            for label, value in fields.items()
            if value is not None
        ],
    }


def _presentation(title: str, description: str = "") -> dict:
    return {
        "schema_version": "1.0",
        "capability": DOMAIN,
        "view_type": "cards",
        "title": title,
        "description": description,
        "provenance": {"source": "procurement", "source_label": "采购资料", "is_estimated": False},
        "limitations": [],
    }


def _line_card(line: dict, index: int) -> dict:
    unit = line["unit"] or ""
    quantity = line["quantity"]
    assumption = (
        f"{line['people']} 人 × 每人 {line['per_person']}{unit}，请核对人均用量"
        if line["quantity_estimated"]
        else None
    )
    return _card(
        f"line-{index}",
        line["item"],
        {
            "需求规格": line["specification"] or "待明确",
            "需求数量": f"{quantity} {unit}" if quantity is not None else "待明确",
            "数量估算": assumption,
        },
    )


def _quote_card(row: dict, index: int) -> dict:
    count, unit = row["actual_quantity"], row["quote_unit"] or ""
    price = _yuan(row["price_cny"])
    basis = "/包" if row["price_basis"] == "pack" else f"/{unit}"
    total, subtotal = _yuan(row["total_cny"]), _yuan(row["known_subtotal_cny"])
    fee_labels = {
        "shipping_cny": "运费",
        "tax_cny": "另计税费",
        "fees_cny": "附加费用",
        "discount_cny": "本单优惠",
    }
    budget = {"within": "在预算内", "exceeded": "超出预算", "unconfirmed": "待核对"}
    arrival = row["arrival_date"]
    return _card(
        f"quote-{index}",
        f"{row['item']} · {row['supplier']}",
        {
            "来源": "用户提供的报价",
            "报价日期": row["quoted_on"] or "资料未注明",
            "报价规格": row["specification"] or "待核对",
            "单位与每包数量": f"每包 {row['pack_size']} {unit}" if row["pack_size"] else None,
            "起订": f"{row['minimum_quantity']} {unit}" if row["minimum_quantity"] else None,
            "实买数量": f"{count} {unit}，多买 {row['extra_quantity']} {unit}" if count else None,
            "适用价": f"{price}{basis}" if price else None,
            "商品金额": _yuan(row["goods_cny"])
            if row["goods_cny"] != row["known_subtotal_cny"]
            else None,
            **{label: _yuan(row["fees"][field]) for field, label in fee_labels.items()},
            "总费用": total,
            "已知小计": subtotal if total is None else None,
            "预算": budget.get(row.get("budget_status")),
            "到货": f"{arrival}（{'承诺' if row['arrival_confirmed'] else '预计'}）"
            if arrival
            else None,
            "发货或售后条件": row["delivery_terms"],
            "原文": row["evidence"],
        },
        "；".join(row["issues"]) or None,
        row["url"],
    )


def _plan_card(plan: dict, index: int) -> dict:
    lines = "、".join(
        f"{row['item']} × {row['quantity']}（{row['supplier']}）" for row in plan["lines"]
    )
    budget = {"within": "在预算内", "exceeded": "超出预算", "unconfirmed": "待核对"}
    return _card(
        f"plan-{index}",
        f"采购组合 {index + 1}",
        {
            "实买清单": lines,
            "总费用": _yuan(plan["total_cny"]),
            "已知小计": _yuan(plan["known_subtotal_cny"]) if plan["total_cny"] is None else None,
            "预算": budget.get(plan["budget_status"]),
            "到货待核对项": plan["delivery_gaps"] or None,
        },
    )


def _catalog_cards(sources: list[dict]) -> list[dict]:
    cards = []
    shown_errors = set()
    for index, source in enumerate(sources):
        if source.get("status") != "ok":
            problem = source.get("message")
            if problem and problem not in shown_errors:
                shown_errors.add(problem)
                cards.append(_card(f"source-{index}", "淘宝联盟", {}, problem))
            continue
        cards.extend(
            _provider_card(row, source, f"source-{index}-{position}")
            for position, row in enumerate(source.get("items", [])[:3])
        )
    return cards


def _provider_card(row: dict, source: dict, key: str) -> dict:
    common = {"来源": "淘宝联盟", "查询时间": source.get("fetched_at"), "商品 ID": row["item_id"]}
    if "activity_id" in row:
        return _card(
            key,
            "推广券资料",
            {
                **common,
                "券面金额": _yuan(row["amount_cny"]),
                "使用门槛": _yuan(row["minimum_spend_cny"]),
                "有效期": f"{row['starts_on']} 至 {row['ends_on']}"
                if row["starts_on"] and row["ends_on"]
                else None,
                "剩余量": row["remaining"],
            },
            "个人资格与本批适用条件待核对。",
        )
    return _card(
        key,
        row["title"],
        {
            **common,
            "店铺": row["shop"],
            "商品标价": _yuan(row["reference_price_cny"]),
            "平台预估价": _yuan(row["estimated_price_cny"]),
            "链接": "推广链接" if row["promotion_link"] else None,
        },
        "待核对所选规格、包装和实买价格。",
        row["url"],
    )


async def _compare_report(query: _ReportQuery) -> dict:
    intake = query.intake
    is_list = intake.procedure == Procedure.LIST
    result = compare(
        intake.needs,
        intake.quotes,
        people=intake.people,
        budget=intake.budget_cny,
        today=query.today,
        needed_by=intake.needed_by,
        build_list=is_list,
    )
    title = "采购清单" if is_list else "采购比价"
    cards = [
        *(_line_card(line, i) for i, line in enumerate(result["lines"])),
        *(_plan_card(plan, i) for i, plan in enumerate(result["plans"])),
        *(_quote_card(row, i) for i, row in enumerate(result["quotes"])),
        *_catalog_cards(query.sources),
    ]
    missing = list(dict.fromkeys(issue for row in result["quotes"] for issue in row["issues"]))
    if intake.needs and (missing or not result["quotes"]):
        names = "、".join(need.item for need in intake.needs)
        terms = [issue[2:] for issue in missing if issue.startswith(("核对", "确认"))]
        message = "、".join(terms[:5]) or "具体规格、包装、起订量、含税价、运费与到货时间"
        cards.append(_card("inquiry", "询价草稿", {}, f"您好，计划采购{names}，请提供{message}。"))
    context = [
        f"预算 {_yuan(result['budget_cny'])}" if result["budget_cny"] is not None else None,
        f"最迟 {result['needed_by']} 到货" if result["needed_by"] else None,
    ]
    return {
        **result,
        **_presentation(title, " · ".join(value for value in context if value)),
        "sources": query.sources,
        "items": cards,
    }


def _check_line(line: dict) -> str:
    expected = line["expected"] if line["expected"] is not None else "待核对"
    received = line["received"] if line["received"] is not None else "待核对"
    difference = line["difference"]
    mismatch = f"，差 {difference:+d}" if difference is not None else ""
    spec = {"matched": "规格一致", "different": "规格有差异", "unconfirmed": "规格待核对"}
    return f"应到 {expected}，实到 {received}{mismatch}，{spec[line['specification_status']]}"


async def _ledger_report(query: _ReportQuery) -> dict:
    result = reconcile(query.intake.records)
    cards = [
        _card(
            f"order-{i}",
            f"订单 {order['reference']}",
            {
                **{line["item"] or "数量核对": _check_line(line) for line in order["lines"]},
                "已付款": _yuan(order["paid_cny"]),
                "确认到账退款": _yuan(order["refunded_cny"]),
                "净支出": _yuan(order["net_paid_cny"]),
                "待到账退款记录": order["pending_refunds"] or None,
                "资料": " / ".join(order["evidence"]),
            },
            "；".join(order["issues"]) or None,
        )
        for i, order in enumerate(result["orders"])
    ]
    return {**result, **_presentation("采购与到货核对"), "items": cards}


def procurement_tools(
    *,
    app_key: SecretStr = SecretStr(""),
    app_secret: SecretStr = SecretStr(""),
    adzone_id: str = "",
) -> ToolRegistry:
    client = TaobaoAffiliate(app_key, app_secret, adzone_id)

    async def catalog(query: _CatalogQuery) -> dict:
        lookup = query.lookup
        if lookup:
            receipts = [client.details(lookup.item_id)]
            if lookup.activity_id:
                receipts.append(client.coupon(lookup.item_id, lookup.activity_id))
        else:
            receipts = [
                client.search(keyword, tmall_only=query.tmall_only) for keyword in query.keywords
            ]
        return {"sources": await asyncio.gather(*receipts)}

    return ToolRegistry(
        [
            _Tool(CATALOG, "读取已授权的淘宝联盟商品、详情或推广券资料。", _CatalogQuery, catalog),
            _Tool(
                COMPARE,
                "对齐规格、包装、阶梯价和已知费用。",
                _ReportQuery,
                _compare_report,
                render_hint="cards",
            ),
            _Tool(
                BUILD_LIST,
                "汇总多物品数量、选项和采购组合。",
                _ReportQuery,
                _compare_report,
                render_hint="cards",
            ),
            _Tool(
                RECONCILE,
                "按订单关联应到、实到、付款、发票与退款资料。",
                _ReportQuery,
                _ledger_report,
                render_hint="cards",
            ),
        ]
    )
