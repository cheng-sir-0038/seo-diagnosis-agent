"""Register the workspace's shopping and procurement workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.registry import (
    PublicInstruction,
    PublicSkill,
    RoutingTurn,
    SpecialistDefinition,
    SpecialistProfile,
    ToolContext,
)
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.procurement_agent.models import DOMAIN, INTAKE_PROMPT, Procedure, ProcurementIntake
from src.procurement_agent.search_intent import is_catalog_refresh_followup
from src.procurement_agent.tools import (
    BUILD_LIST,
    CATALOG,
    COMPARE,
    PROCUREMENT_SKILL,
    RECONCILE,
    procurement_tools,
)
from src.procurement_agent.workflow import run_procurement

if TYPE_CHECKING:
    from src.agent.workspace.execution import SpecialistExecution


class ProcurementRouteInput(BaseModel):
    procurement: ProcurementIntake | None = None


async def build_procurement_tools(context: ToolContext) -> ToolRegistry:
    """Create live procurement tools with the workspace catalog settings and cache."""
    settings = context.resources.settings
    return procurement_tools(
        app_key=settings.taobao_tbk_app_key,
        app_secret=settings.taobao_tbk_app_secret,
        adzone_id=settings.taobao_tbk_adzone_id,
        local_catalog=settings.taobao_tbk_local_catalog,
        cache=context.resources.cache,
    )


def prepare_procurement_route(turn: RoutingTurn) -> str | None:
    """Checkpoint extracted conditions without treating product links as page targets."""
    turn.state.pop("target_url", None)
    intake = getattr(turn.decision, "procurement", None) or ProcurementIntake()
    turn.state["procurement_intake"] = intake.model_dump(mode="json")
    return None


def inherit_procurement_context(previous: dict[str, Any]) -> dict[str, Any]:
    """Carry the last catalog search's product needs and filters into a new run."""
    procurement = previous.get("procurement")
    catalog_search = None
    if (
        previous.get("route") == DOMAIN and isinstance(procurement, dict)
        and procurement.get("procedure") == Procedure.SEARCH and procurement.get("needs")
    ):
        catalog_search = {
            "needs": procurement["needs"],
            "tmall_only": procurement.get("tmall_only"),
        }
    return {"previous_catalog_search": catalog_search}


def procurement_routing_context(saved: dict[str, Any], pending_route: str | None) -> dict[str, Any]:
    """Expose the previous search criteria to structured routing."""
    return {"previous_catalog_search": saved.get("previous_catalog_search")}


def procurement_refresh_shortcut(latest: str, saved: dict[str, Any]) -> dict[str, Any] | None:
    """Recognize a standalone refresh request and reuse the previous search filters."""
    if saved.get("previous_route") != DOMAIN or not is_catalog_refresh_followup(latest):
        return None
    intake = ProcurementIntake(
        procedure=Procedure.SEARCH, **(saved.get("previous_catalog_search") or {}),
    )
    return {"route": DOMAIN, "needs_page": False, "procurement": intake}


async def execute_workspace_procurement(execution: SpecialistExecution) -> None:
    """Continue shopping or procurement with the saved conditions and receipts."""
    run, executor = execution.run, execution.executor
    async with observed_operation(
        execution.resources.observability, "workspace.procurement", kind=SpanKind.AGENT,
        input_data={"skill": PROCUREMENT_SKILL.name},
        metadata={"agent_profile": execution.definition.profile.public_data()},
    ) as span:
        executor.tools = await execution.tools()
        await execution.record_skill(PROCUREMENT_SKILL)
        run.dispatch_state["active_agent"] = execution.definition.id
        await run_procurement(executor, run, execution.resources.planner)
        if span:
            span.output_data = {"status": run.status.value}


PROCUREMENT_SPECIALIST = SpecialistDefinition(
    id=DOMAIN,
    profile=SpecialistProfile("购物与采购", "cart", "amber", "procurement"),
    routing_prompt=PROCUREMENT_SKILL.prompt_fragment + (
        "\n仅在选择 shopping_procurement 后填写 procurement，needs_page 设为 false。"
    ) + INTAKE_PROMPT,
    tools=build_procurement_tools,
    execute=execute_workspace_procurement,
    intake=ProcurementRouteInput,
    prepare=prepare_procurement_route,
    inherit=inherit_procurement_context,
    routing_context=procurement_routing_context,
    shortcut=procurement_refresh_shortcut,
    directory_note="比较报价、列采购清单和核对到货。",
    deterministic_path="条件确认后的报价计算、清单汇总与到货核对按固定规则执行，无额外提示词。",
    public_skills=(PublicSkill.publish(
        PROCUREMENT_SKILL,
        summary="按规格搜索商品，比较报价，整理采购清单并核对实到数量和支出。",
        instruction=(
            "先确认要找商品参考价、比较商家报价、整理采购清单，还是按凭证核对到货和支出。"
            "查商品时按已明确的品名、规格和筛选条件查询，刷新时沿用已确认条件并采用用户的更正。"
            "比价按规格、计量单位、整包装和起订量算实买量，区分已知小计与完整成本。"
            "清单区分已确认的数量和估算量。按订单归集实收、付款和到账退款，标出可核实的差异和缺少的凭证。"
            "实际下单与付款由用户在平台办理。"
        ),
        usage={
            CATALOG: (
                "找商品和参考价，或查单件商品详情及推广券资料。",
                "搜索时给出品名和已有规格，可指定只看天猫。查详情请提供商品编号或含编号的淘宝、天猫商品链接，查券再提供券编号。",
                "按工作台配置从淘宝联盟或本地商品库查关键词。商品详情和券资料需相应的平台权限。",
                "给出匹配商品的标价、参考价、来源及链接，或列出推广券的面额、门槛和有效期。实际价格和用券资格以结算为准。",
            ),
            COMPARE: (
                "比较已有商家报价，算出按包装和起订量实际要买多少、已知要花多少。",
                "提供规格、需求数量和计量单位，附商家报价原文，最好含每包数量、起订量、价格及费用明细。可附预算和最迟到货日。",
                "对齐规格和单位，按起订量及整包装量算实买数量，再匹配有效阶梯价。只计入可核实的费用和本单适用的优惠。",
                "逐份列出实买量、超买量、适用价和已知小计。费用齐全时给出总价，有到货要求时对照商家的送达承诺。",
            ),
            BUILD_LIST: (
                "为活动或多项物品汇总采购需求，并组合能覆盖整份清单的报价。",
                "列出各项品名、规格、单位和数量，或提供人数和人均用量供估算。可附各项报价、总预算和最迟到货日。",
                "明确的需求量直接采用，按人数推算的用量单独标注。各项都有可核算的商品报价且无已知逾期时才组成采购方案。",
                "给出逐项需求清单和可用报价，条件具备时列出实买组合及费用。缺少报价或关键条款时附询价草稿。",
            ),
            RECONCILE: (
                "按订单核对实收数量、规格和实际采购支出。",
                "贴订单号和已有订单、收货、付款、发票或退款凭证原文。有分笔凭证时注明各自编号。",
                "按订单归集你提供的凭证，相同凭证只计一次。净支出用已付款扣除已到账退款，发票作为凭证保留。",
                "逐单列出应到、实到、可核实的数量和规格差异，以及已付款、到账退款和可核算的净支出。凭证不足时指出缺的是哪一项。",
            ),
        },
    ),),
    public_instructions=(PublicInstruction(
        id="procurement-intake",
        label="采购条件提取",
        text=(
            "按物品记录明确的品名、规格、计量单位和需求数量。按人数推算的用量单独标注，"
            "预算和到货期限只记录用户提出的条件。报价和凭证从最新消息逐字摘录，保留商家或订单号、"
            "价格口径、装量、起订量及实际承诺。金额和到货条件缺少依据时保留未知。"
            "分别记录订单、实收、付款、发票和退款，只用明确到账的退款核算净支出。"
        ),
    ),),
)
