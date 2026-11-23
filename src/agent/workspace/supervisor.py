from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from src.agent.models import AgentRun, HumanInputState
from src.agent.providers.base import ToolCall
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.routing import (
    DISPATCHED_EVENT,
    ROUTE_PROMPT_KEY,
    SKILL_LOADED_EVENT,
    SUPERVISOR_NODE,
    Route,
    RouteDecision,
    Specialist,
    explicit_healthcare_skill,
    page_candidates,
    select_page,
)
from src.healthcare_agent import public_rule_evidence
from src.healthcare_agent.exams import MedicalExamSyllabus
from src.healthcare_agent.skills import (
    HealthcareSkill,
    render_healthcare_skill,
    skill_for_tool,
    verified_healthcare_answer,
)
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.workflows.course_review import course_editor_input
from src.seo_agent.workflows.structured_model import StructuredModelRunner

SeoSpecialist = Callable[[str | None, list[str]], Awaitable[tuple[str, ToolRegistry]]]
_SUPERVISOR_INPUT = "workspace_clarification"


class GraphState(TypedDict):
    run: AgentRun
    next_node: str


def conversation_context(previous: dict[str, Any]) -> dict[str, Any]:
    """仅从同一对话的完成记录继承目标和上一轮业务。"""
    return {
        "target_url": previous.get("target_url"),
        "previous_route": previous.get("route"),
        "previous_run_id": previous.get("source_run_id"),
        "previous_agent": previous.get("active_agent"),
        "previous_healthcare_skill": previous.get("healthcare_skill"),
    }


def _dialog(run: AgentRun) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": str(message["content"])}
        for message in run.messages
        if message["role"] in {"user", "assistant"} and message.get("content")
        and not message.get("tool_calls")
    ]


class WorkspaceSupervisor:
    def __init__(
        self, executor: AgentExecutor, planner: StructuredModelRunner, seo: SeoSpecialist,
        medical_tools: Callable[[], ToolRegistry],
        load_dialog: Callable[[str, str, str], Awaitable[list[dict[str, str]]]],
    ) -> None:
        self.executor, self.planner, self.seo = executor, planner, seo
        self.medical_tools, self.load_dialog = medical_tools, load_dialog
        self.client = executor.observer.client if executor.observer else None
        graph = StateGraph(GraphState)
        graph.add_node(SUPERVISOR_NODE, self.plan)
        graph.add_node(Specialist.HEALTHCARE, self.healthcare)
        graph.add_node(Specialist.SEO, self.search)
        graph.add_node(Route.CLARIFY, self.clarify)
        graph.add_edge(START, SUPERVISOR_NODE)
        graph.add_conditional_edges(SUPERVISOR_NODE, lambda state: state["next_node"], {
            node: node for node in (Specialist.HEALTHCARE, Specialist.SEO, Route.CLARIFY)
        })
        graph.add_conditional_edges(Specialist.HEALTHCARE, lambda state: state["next_node"], {
            Specialist.SEO: Specialist.SEO, END: END,
        })
        graph.add_edge(Specialist.SEO, END)
        graph.add_edge(Route.CLARIFY, END)
        self.graph = graph.compile()

    async def execute(self, run: AgentRun) -> None:
        await self.graph.ainvoke({"run": run, "next_node": SUPERVISOR_NODE})

    async def plan(self, state: GraphState) -> dict[str, str]:
        run = state["run"]
        saved = run.dispatch_state
        receipt = run.human_input
        answered = bool(receipt and receipt.tool_name == _SUPERVISOR_INPUT and receipt.answer)
        if not saved.get("route") or answered:
            dialog = _dialog(run)
            latest = next(m["content"] for m in reversed(dialog) if m["role"] == "user")
            targets, invalid = page_candidates(latest)
            new_targets = bool(targets)
            if answered and not targets and not invalid:
                targets = saved.get("pending_targets", [])
            async with observed_operation(
                self.client, "workspace.supervisor", kind=SpanKind.AGENT,
                input_data={"request": latest, "target_candidates": targets},
            ) as span:
                if answered and saved.get("awaiting_page") and new_targets and len(targets) == 1:
                    decision = RouteDecision(route=saved["route"], needs_page=True, target_index=1)
                else:
                    previous = await self._previous_dialog(run, saved.get("previous_agent"))
                    result = await self.planner.invoke(
                        prompt_key=ROUTE_PROMPT_KEY, schema=RouteDecision, method="json_mode",
                        context={
                            "conversation": [
                                {**m, "content": m["content"][-2000:]} for m in dialog[-6:]
                            ],
                            "previous_route": saved.get("route") or saved.get("previous_route"),
                            "previous_healthcare_skill": saved.get("previous_healthcare_skill"),
                            "previous_intent": next(
                                (m["content"] for m in reversed(previous) if m["role"] == "user"),
                                None,
                            ),
                            "current_page": saved.get("target_url"),
                            "candidate_pages": targets,
                        },
                        operation="workspace.route", business_object={"run_id": run.id},
                    )
                    decision = result.output
                    for key, count in result.usage.items():
                        run.token_usage[key] = run.token_usage.get(key, 0) + count
                question = decision.question if decision.route == Route.CLARIFY else None
                if decision.route == Route.CLARIFY:
                    question = question or "你想检查网页，还是查考试、医保或医学文献资料？"
                if decision.route in {Route.SEO, Route.COURSE_REVIEW}:
                    saved["target_url"], question = select_page(
                        saved.get("target_url"), targets, invalid, decision,
                    )
                wait_for_target = bool(question and decision.route != Route.CLARIFY)
                if decision.route == Route.HEALTHCARE:
                    saved["healthcare_skill"] = (
                        explicit_healthcare_skill(latest) or decision.healthcare_skill
                    )
                saved.update(
                    route=decision.route, question=question, awaiting_page=wait_for_target,
                    pending_targets=targets if wait_for_target else [],
                )
                if not answered or not saved.get("intent"):
                    saved["intent"] = latest[:2000]
                if answered and receipt:
                    await self.executor.emit(run, "input.accepted", {
                        "request_id": receipt.request_id, "answer": receipt.answer,
                        "action": receipt.action,
                    })
                    run.human_input = None
                await self.executor.checkpoint(run, stage="route_selected")
                if span:
                    span.output_data = {
                        "route": decision.route, "target_url": saved.get("target_url"),
                        "question": question, "skill": saved.get("healthcare_skill"),
                    }
        if saved.get("question"):
            return {"next_node": Route.CLARIFY}
        if saved["route"] == Route.COURSE_REVIEW:
            next_agent = Specialist.SEO if saved.get("public_rule") else Specialist.HEALTHCARE
            return {"next_node": next_agent}
        return {"next_node": saved["route"]}

    async def clarify(self, state: GraphState) -> dict:
        run = state["run"]
        if run.human_input is None:
            run.human_input = HumanInputState(
                questions=[run.dispatch_state["question"]], request_id=f"input_{uuid4().hex}",
                tool_name=_SUPERVISOR_INPUT, tool_arguments={}, tool_result={},
            )
        await self.executor.run_turns(run)
        return {}

    async def _announce(self, run: AgentRun, agent: Specialist) -> None:
        already_sent = any(
            event.type == DISPATCHED_EVENT and event.payload.get("agent") == agent
            for event in run.events
        )
        if not already_sent:
            await self.executor.emit(
                run, DISPATCHED_EVENT, {"from": SUPERVISOR_NODE, "agent": agent},
            )

    async def _previous_dialog(self, run: AgentRun, agent: str | None) -> list[dict[str, str]]:
        saved = run.dispatch_state
        source = saved.get("previous_run_id")
        if (
            not source or not agent or not run.conversation_id
            or saved.get("previous_agent") != agent
        ):
            return []
        return await self.load_dialog(source, run.conversation_id, agent)

    async def _specialist_dialog(self, run: AgentRun, agent: Specialist) -> list[dict[str, str]]:
        dialog = _dialog(run)
        if run.dispatch_state.get("active_agent"):
            return dialog
        return [*await self._previous_dialog(run, agent), *dialog]

    async def healthcare(self, state: GraphState) -> dict[str, str]:
        run = state["run"]
        saved = run.dispatch_state
        course_review = saved["route"] == Route.COURSE_REVIEW
        previous_tool = run.human_input.tool_name if run.human_input else None
        if previous_tool is None and run.pending_tool_calls:
            previous_tool = run.pending_tool_calls[0]["name"]
        skill_id = (
            HealthcareSkill.SYLLABUS if course_review else saved.get("healthcare_skill")
            or (skill_for_tool(previous_tool) if previous_tool else None)
            or explicit_healthcare_skill(saved.get("intent", ""))
        )
        async with observed_operation(
            self.client, "workspace.healthcare", kind=SpanKind.AGENT,
            input_data={"route": saved["route"], "skill": skill_id},
        ) as span:
            await self._announce(run, Specialist.HEALTHCARE)
            registry = self.medical_tools()
            try:
                skill, self.executor.tools, prompt = render_healthcare_skill(skill_id, registry)
            except KeyError:
                self.executor.tools = ToolRegistry()
                run.pending_tool_calls = []
                run.human_input = None
                text = (
                    "当前无法读取对应的公开资料。可以换个问题，或稍后重新查询。"
                    if skill_id else
                    "可以查公开的考研招生、医考公告、医保药品目录和医学文献。"
                    "门店优惠、会员、库存和实时药价需要由门店提供数据。"
                )
                await self.executor.run_turns(run, evidence_answer=lambda _: text)
                return {"next_node": END}
            saved["healthcare_skill"] = skill_id
            skill_event = {
                "agent": Specialist.HEALTHCARE, "skill": skill.name, "label": skill.description,
                "tools": self.executor.tools.names(), "prompt_hash": prompt.prompt_hash,
            }
            if not any(event.type == SKILL_LOADED_EVENT for event in run.events):
                async with observed_operation(
                    self.client, "workspace.skill", kind=SpanKind.INTERNAL, input_data=skill_event,
                ):
                    await self.executor.emit(run, SKILL_LOADED_EVENT, skill_event)
            if course_review:
                tool = MedicalExamSyllabus.name
                result = await self.executor.required_tool(
                    run, ToolCall("handoff_syllabus", tool, {})
                )
                evidence = public_rule_evidence(tool, result)
                if not evidence:
                    raise ValueError("本次未取得可交接的公开考纲依据，请稍后再试。")
                saved["public_rule"] = evidence
                await self.executor.checkpoint(run, stage="public_rule_verified")
                if span:
                    span.output_data = {"recipient": Specialist.SEO, "public_rule": evidence}
                return {"next_node": Specialist.SEO}
            dialog = (
                run.messages[1:] if saved.get("active_agent")
                else await self._specialist_dialog(run, Specialist.HEALTHCARE)
            )
            run.messages = [{"role": "system", "content": prompt.system_prompt}, *dialog]
            saved["active_agent"] = Specialist.HEALTHCARE
            await self.executor.checkpoint(run, stage="healthcare_ready")
            await self.executor.run_turns(
                run,
                evidence_answer=lambda current: verified_healthcare_answer(current, skill.tools[0]),
            )
            if span:
                span.output_data = {"status": run.status.value}
        return {"next_node": END}

    async def search(self, state: GraphState) -> dict:
        run = state["run"]
        saved = run.dispatch_state
        target = saved.get("target_url")
        rule = saved.get("public_rule")
        if rule:
            dialog = [{"role": "user", "content": f"请用已核实的考纲条款核对课程页 {target}。"}]
        else:
            dialog = await self._specialist_dialog(run, Specialist.SEO)
        prompt, registry = await self.seo(
            target, [m["content"] for m in dialog if m["role"] == "user"],
        )
        self.executor.tools = registry
        async with observed_operation(
            self.client, "workspace.seo", kind=SpanKind.AGENT,
            input_data={"target_url": target, "public_rule": rule},
        ) as span:
            await self._announce(run, Specialist.SEO)
            if not saved.get("active_agent"):
                if rule:
                    page = await self.executor.required_tool(
                        run, ToolCall("handoff_course_page", "seo_page_metadata", {"url": target})
                    )
                    prompt, dialog = course_editor_input(target, rule, page)
                run.messages = [{"role": "system", "content": prompt}, *dialog]
                saved["active_agent"] = Specialist.SEO
                await self.executor.checkpoint(run, stage="seo_ready")
            if rule:
                # Both evidence reads are complete. The editor receives exactly that material.
                self.executor.tools = ToolRegistry()
            await self.executor.run_turns(run)
            if span:
                span.output_data = {"target_url": target, "status": run.status.value}
        return {}
