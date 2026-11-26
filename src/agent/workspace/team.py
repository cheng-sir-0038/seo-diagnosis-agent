"""Dispatch workspace runs to specialists and coordinate cross-specialist handoffs."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from src.agent.config import Skill
from src.agent.models import AgentRun, HumanInputState
from src.agent.providers.base import ToolCall
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.routing import (
    ROUTE_PROMPT_KEY,
    SKILL_LOADED_EVENT,
    SUPERVISOR_NODE,
    Route,
    RouteDecision,
    Specialist,
    explicit_healthcare_skill,
    is_bare_page_reply,
    page_candidates,
    select_page,
)
from src.education_agent.practice import MAX_QUESTIONS, MathPractice
from src.education_agent.resources import LearningResources
from src.education_agent.skills import (
    EDUCATION_SKILLS,
    EducationSkill,
    education_tools,
    learning_history,
)
from src.education_agent.teacher_exam import TeacherExamQuery
from src.followup_agent.store import FollowupCaseStore
from src.followup_agent.workflow import FOLLOWUP_SKILL, followup_tools, run_followup
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
from src.procurement_agent.models import Procedure, ProcurementIntake
from src.procurement_agent.search_intent import is_catalog_refresh_followup
from src.procurement_agent.tools import PROCUREMENT_SKILL, procurement_tools
from src.procurement_agent.workflow import run_procurement
from src.seo_agent.specialist import SeoSpecialistFactory, run_seo_specialist
from src.seo_agent.workflows.structured_model import StructuredModelRunner
from src.travel_agent.intake import TravelIntake, china_today
from src.travel_agent.tools import TRAVEL_SKILL, travel_tools
from src.travel_agent.workflow import run_travel

_SUPERVISOR_INPUT = "workspace_clarification"


class GraphState(TypedDict):
    """Carry the shared run and the next graph edge for the current invocation."""

    run: AgentRun
    next_node: str


def conversation_context(previous: dict[str, Any]) -> dict[str, Any]:
    """Seed a new run with routing context from the same conversation's completed run.

    The API supplies the prior dispatch snapshot. Specialist dialogue is loaded
    separately and only for the specialist that owned it.
    """
    procurement = previous.get("procurement")
    catalog_search = None
    if (
        previous.get("route") == Route.PROCUREMENT and isinstance(procurement, dict)
        and procurement.get("procedure") == Procedure.SEARCH and procurement.get("needs")
    ):
        catalog_search = {
            "needs": procurement["needs"],
            "tmall_only": procurement.get("tmall_only"),
        }
    return {
        "target_url": previous.get("target_url"),
        "previous_route": previous.get("route"),
        "previous_run_id": previous.get("source_run_id"),
        "previous_agent": previous.get("active_agent"),
        "previous_healthcare_skill": previous.get("healthcare_skill"),
        "previous_teacher_exam": previous.get("teacher_exam"),
        "previous_catalog_search": catalog_search,
    }


def _dialog(run: AgentRun) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": str(message["content"])}
        for message in run.messages
        if message["role"] in {"user", "assistant"} and message.get("content")
        and not message.get("tool_calls")
    ]


class WorkspaceTeam:
    """Run the supervisor, clarification and specialist nodes within one AgentRun.

    The graph starts at the supervisor on each invocation. The executor's
    checkpoints carry dispatch state, dialogue and tool receipts across resumes.
    """

    def __init__(
        self, executor: AgentExecutor, planner: StructuredModelRunner, seo: SeoSpecialistFactory,
        medical_tools: Callable[[], ToolRegistry],
        load_dialog: Callable[[str, str, str], Awaitable[list[dict[str, str]]]],
        load_tool_data: Callable[[str, tuple[str, ...]], Awaitable[list[dict[str, Any]]]] | None,
        followup_store: FollowupCaseStore | None = None,
        travel_registry: Callable[[], ToolRegistry] = travel_tools,
        procurement_registry: Callable[[], ToolRegistry] = procurement_tools,
    ) -> None:
        self.executor, self.planner, self.seo = executor, planner, seo
        self.medical_tools, self.load_dialog = medical_tools, load_dialog
        self.load_tool_data = load_tool_data
        self.followup_store = followup_store
        self.travel_registry = travel_registry
        self.procurement_registry = procurement_registry
        self.client = executor.observer.client if executor.observer else None
        graph = StateGraph(GraphState)
        graph.add_node(SUPERVISOR_NODE, self.plan)
        specialists = {
            Specialist.HEALTHCARE: self.healthcare,
            Specialist.SEO: self.search,
            Specialist.LEARNING: self.education,
            Specialist.EXAM_PREP: self.education,
            Specialist.FOLLOWUP: self.followup,
            Specialist.TRAVEL: self.travel,
            Specialist.PROCUREMENT: self.procurement,
        }
        for agent, node in specialists.items():
            graph.add_node(agent, node)
        graph.add_node(Route.CLARIFY, self.clarify)
        graph.add_edge(START, SUPERVISOR_NODE)
        graph.add_conditional_edges(SUPERVISOR_NODE, lambda state: state["next_node"], {
            node: node for node in (*specialists, Route.CLARIFY)
        })
        graph.add_conditional_edges(Specialist.HEALTHCARE, lambda state: state["next_node"], {
            Specialist.SEO: Specialist.SEO, END: END,
        })
        direct = specialists.keys() - {Specialist.HEALTHCARE}
        for node in (*direct, Route.CLARIFY):
            graph.add_edge(node, END)
        self.graph = graph.compile()

    async def execute(self, run: AgentRun) -> None:
        """Enter the team graph with a fresh or restored run from the executor."""
        await self.graph.ainvoke({"run": run, "next_node": SUPERVISOR_NODE})

    async def plan(self, state: GraphState) -> dict[str, str]:
        """Select and checkpoint a route, or reuse the saved route on resume.

        A supervisor clarification answer can refine the route. A course review
        with a saved public rule proceeds directly to the SEO specialist.
        """
        run = state["run"]
        saved = run.dispatch_state
        receipt = run.human_input
        answered = bool(receipt and receipt.tool_name == _SUPERVISOR_INPUT and receipt.answer)
        if not saved.get("route") or answered:
            dialog = _dialog(run)
            latest = next(m["content"] for m in reversed(dialog) if m["role"] == "user")
            targets, invalid = page_candidates(latest)
            pending_route = saved.get("route") if answered else None
            bare_page_answer = (
                pending_route in {Route.SEO, Route.COURSE_REVIEW}
                and saved.get("awaiting_page") and len(targets) == 1 and not invalid
                and is_bare_page_reply(latest)
            )
            if answered and not targets and not invalid:
                targets = saved.get("pending_targets", [])
            async with observed_operation(
                self.client, "workspace.supervisor", kind=SpanKind.AGENT,
                input_data={"request": latest, "target_candidates": targets},
            ) as span:
                if bare_page_answer:
                    decision = RouteDecision(route=pending_route, needs_page=True, target_index=1)
                elif (
                    not answered and saved.get("previous_route") == Route.PROCUREMENT
                    and is_catalog_refresh_followup(latest)
                ):
                    checkout = ProcurementIntake(
                        procedure=Procedure.SEARCH, **(saved.get("previous_catalog_search") or {})
                    )
                    decision = RouteDecision(
                        route=Route.PROCUREMENT, needs_page=False, procurement=checkout
                    )
                else:
                    previous = await self._previous_dialog(run, saved.get("previous_agent"))
                    result = await self.planner.invoke(
                        prompt_key=ROUTE_PROMPT_KEY, schema=RouteDecision, method="json_mode",
                        context={
                            "current_date": china_today().isoformat(),
                            "conversation": [
                                {**m, "content": m["content"][-2000:]} for m in dialog[-6:]
                            ],
                            "previous_route": pending_route or saved.get("previous_route"),
                            "pending_intent": saved.get("intent") if answered else None,
                            "awaiting_page": bool(answered and saved.get("awaiting_page")),
                            "previous_healthcare_skill": saved.get("previous_healthcare_skill"),
                            "previous_teacher_exam": saved.get("previous_teacher_exam"),
                            "previous_catalog_search": saved.get("previous_catalog_search"),
                            "pending_teacher_exam": (
                                saved.get("teacher_exam") if pending_route == Route.EXAM_PREP else None
                            ),
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
                    question = question or (
                        "你想检查网页、查资料、练题、整理复诊记录，还是规划出差？"
                    )
                if decision.route in {Route.SEO, Route.COURSE_REVIEW}:
                    saved["target_url"], question = select_page(
                        saved.get("target_url"), targets, invalid, decision,
                    )
                wait_for_target = bool(question and decision.route != Route.CLARIFY)
                if decision.route == Route.COURSE_REVIEW:
                    if pending_route == Route.COURSE_REVIEW:
                        requests = saved.get("course_review_requests") or (
                            [saved["intent"]] if saved.get("intent") else []
                        )
                        if not bare_page_answer:
                            requests = [*requests, latest]
                    else:
                        requests = [latest]
                    saved["course_review_requests"] = requests
                else:
                    saved.pop("course_review_requests", None)
                if decision.route == Route.HEALTHCARE:
                    explicit_skill = explicit_healthcare_skill(latest)
                    if answered and decision.healthcare_skill:
                        # A correction may mention a cancelled healthcare topic.
                        saved["healthcare_skill"] = decision.healthcare_skill
                    else:
                        saved["healthcare_skill"] = explicit_skill or decision.healthcare_skill
                elif decision.route == Route.LEARNING:
                    if decision.learning_skill:
                        request = (
                            decision.math_practice
                            if decision.learning_skill == EducationSkill.MATH_PRACTICE
                            else decision.resources
                        )
                        saved.update(
                            learning_skill=decision.learning_skill,
                            learning_arguments=request.model_dump(mode="json") if request else {},
                        )
                    else:
                        question = "你想做数学题，还是找可以跟着练习的官方学习资料？"
                elif decision.route == Route.EXAM_PREP:
                    constraints = (
                        saved.get("teacher_exam", {}) if pending_route == Route.EXAM_PREP else {}
                    )
                    extracted = decision.teacher_exam or TeacherExamQuery()
                    query = TeacherExamQuery.model_validate({
                        **constraints, **extracted.model_dump(exclude_none=True),
                    })
                    question = query.clarification()
                    saved["teacher_exam"] = query.model_dump(mode="json", exclude_none=True)
                elif decision.route == Route.FOLLOWUP:
                    saved.pop("target_url", None)
                elif decision.route == Route.TRAVEL:
                    saved.pop("target_url", None)
                    intake = decision.travel or TravelIntake()
                    saved["travel_intake"] = intake.model_dump(mode="json")
                elif decision.route == Route.PROCUREMENT:
                    saved.pop("target_url", None)
                    checkout = decision.procurement or ProcurementIntake()
                    saved["procurement_intake"] = checkout.model_dump(mode="json")
                saved.update(
                    route=decision.route, question=question, awaiting_page=wait_for_target,
                    pending_targets=targets if wait_for_target else [],
                )
                if not answered or decision.route != pending_route or not saved.get("intent"):
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
                        "question": question, "skill": (
                            saved.get("healthcare_skill") or saved.get("learning_skill")
                            or (EducationSkill.TEACHER_EXAM
                                if decision.route == Route.EXAM_PREP else None)
                        ),
                    }
        if saved.get("question"):
            return {"next_node": Route.CLARIFY}
        if saved["route"] == Route.COURSE_REVIEW:
            next_agent = Specialist.SEO if saved.get("public_rule") else Specialist.HEALTHCARE
            return {"next_node": next_agent}
        return {"next_node": saved["route"]}

    async def clarify(self, state: GraphState) -> dict:
        """Wait on the executor's input receipt and retain its ID on resume."""
        run = state["run"]
        if run.human_input is None:
            run.human_input = HumanInputState(
                questions=[run.dispatch_state["question"]], request_id=f"input_{uuid4().hex}",
                tool_name=_SUPERVISOR_INPUT, tool_arguments={}, tool_result={},
            )
        await self.executor.run_turns(run)
        return {}

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

    async def _record_skill(
        self, run: AgentRun, agent_run_id: str, skill: Skill, prompt_hash: str,
    ) -> None:
        if any(
            event.type == SKILL_LOADED_EVENT and event.payload.get("agent_run_id") == agent_run_id
            for event in run.events
        ):
            return
        payload = {
            "agent": skill.domain, "skill": skill.name, "label": skill.description,
            "tools": self.executor.tools.names(), "prompt_hash": prompt_hash,
        }
        async with observed_operation(
            self.client, "workspace.skill", kind=SpanKind.INTERNAL, input_data=payload,
        ):
            await self.executor.emit(run, SKILL_LOADED_EVENT, payload)

    async def education(self, state: GraphState) -> dict:
        run = state["run"]
        saved = run.dispatch_state
        agent = Specialist(saved["route"])
        if agent == Specialist.EXAM_PREP:
            skill = EDUCATION_SKILLS.get(EducationSkill.TEACHER_EXAM)
            arguments = saved["teacher_exam"]
        else:
            skill = EDUCATION_SKILLS.get(saved["learning_skill"])
            arguments = saved["learning_arguments"]
        tool_name = skill.tools[0]
        async with observed_operation(
            self.client, f"workspace.{agent}", kind=SpanKind.AGENT,
            input_data={"skill": skill.name, "arguments": arguments},
        ) as span, self.executor.specialist(run, agent, sender=SUPERVISOR_NODE) as agent_run_id:
            if (
                agent == Specialist.LEARNING and "learning_history" not in saved
                and self.load_tool_data and run.conversation_id
            ):
                receipts = await self.load_tool_data(
                    run.conversation_id, (MathPractice.name, LearningResources.name),
                )
                saved["learning_history"] = learning_history(receipts)
            registry = education_tools(
                agent, **saved.get("learning_history", {}),
                practice_limit=max(0, min(MAX_QUESTIONS, self.executor.max_tool_calls - 1)),
            )
            self.executor.tools = ToolRegistry(registry.get(name) for name in skill.tools)
            prompt_hash = hashlib.sha256(skill.prompt_fragment.encode()).hexdigest()
            await self._record_skill(run, agent_run_id, skill, prompt_hash)
            saved["active_agent"] = agent

            def answer(current: AgentRun) -> str | None:
                for event in reversed(current.events):
                    if event.type not in {"tool.completed", "tool.failed"} or (
                        event.payload.get("name") != tool_name
                    ):
                        continue
                    result = event.payload["result"]
                    if not result["success"]:
                        return result.get("error") or "本次未能取得资料，请稍后重试。"
                    return result["data"].get("answer")
                return None

            await self.executor.run_tool_workflow(
                run, ToolCall(f"{tool_name}_start", tool_name, arguments), evidence_answer=answer,
            )
            if span:
                span.output_data = {"status": run.status.value}
        return {}

    async def followup(self, state: GraphState) -> dict:
        run = state["run"]
        agent = Specialist.FOLLOWUP
        async with observed_operation(
            self.client, "workspace.followup", kind=SpanKind.AGENT,
            input_data={"skill": FOLLOWUP_SKILL.name},
        ) as span, self.executor.specialist(run, agent, sender=SUPERVISOR_NODE) as agent_run_id:
            self.executor.tools = followup_tools(self.followup_store)
            prompt_hash = hashlib.sha256(FOLLOWUP_SKILL.prompt_fragment.encode()).hexdigest()
            await self._record_skill(run, agent_run_id, FOLLOWUP_SKILL, prompt_hash)
            run.dispatch_state["active_agent"] = agent
            await run_followup(self.executor, run, self.followup_store)
            if span:
                span.output_data = {"status": run.status.value}
        return {}

    async def travel(self, state: GraphState) -> dict:
        run = state["run"]
        async with observed_operation(
            self.client, "workspace.travel", kind=SpanKind.AGENT,
            input_data={"skill": TRAVEL_SKILL.name},
        ) as span, self.executor.specialist(
            run, Specialist.TRAVEL, sender=SUPERVISOR_NODE,
        ) as agent_run_id:
            self.executor.tools = self.travel_registry()
            prompt_hash = hashlib.sha256(TRAVEL_SKILL.prompt_fragment.encode()).hexdigest()
            await self._record_skill(run, agent_run_id, TRAVEL_SKILL, prompt_hash)
            run.dispatch_state["active_agent"] = Specialist.TRAVEL
            await run_travel(self.executor, run, self.planner)
            if span:
                span.output_data = {"status": run.status.value}
        return {}

    async def procurement(self, state: GraphState) -> dict:
        run = state["run"]
        skill = PROCUREMENT_SKILL
        agent = Specialist.PROCUREMENT
        async with observed_operation(
            self.client, "workspace.procurement", kind=SpanKind.AGENT,
            input_data={"skill": skill.name},
        ) as span, self.executor.specialist(run, agent, sender=SUPERVISOR_NODE) as agent_run_id:
            self.executor.tools = self.procurement_registry()
            prompt_hash = hashlib.sha256(skill.prompt_fragment.encode()).hexdigest()
            await self._record_skill(run, agent_run_id, skill, prompt_hash)
            run.dispatch_state["active_agent"] = agent
            await run_procurement(self.executor, run, self.planner)
            if span:
                span.output_data = {"status": run.status.value}
        return {}

    async def healthcare(self, state: GraphState) -> dict[str, str]:
        """Answer a healthcare request or hand verified syllabus evidence to SEO.

        For course reviews, checkpoint the public rule before taking the SEO edge.
        The rule is the handoff payload and carries the source and scope.
        """
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
        ) as span, self.executor.specialist(
            run, Specialist.HEALTHCARE, sender=SUPERVISOR_NODE,
        ) as agent_run_id:
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
            await self._record_skill(run, agent_run_id, skill, prompt.prompt_hash)
            if course_review:
                tool = MedicalExamSyllabus.name
                result = await self.executor.required_tool(
                    run, ToolCall("handoff_syllabus", tool, {})
                )
                evidence = public_rule_evidence(tool, result)
                if not evidence:
                    raise ValueError("本次未取得可交接的公开考纲依据，请稍后再试。")
                saved["public_rule"] = evidence
                await self.executor.complete_specialist(run)
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
        """Dispatch the SEO specialist with its own dialogue or a public-rule handoff."""
        run = state["run"]
        target = run.dispatch_state.get("target_url")
        rule = run.dispatch_state.get("public_rule")
        async with observed_operation(
            self.client, "workspace.seo", kind=SpanKind.AGENT,
            input_data={"target_url": target, "public_rule": rule},
        ) as span, self.executor.specialist(run, Specialist.SEO, sender=SUPERVISOR_NODE):
            dialog = [] if rule else await self._specialist_dialog(run, Specialist.SEO)
            await run_seo_specialist(self.executor, run, configure=self.seo, dialog=dialog)
            if span:
                span.output_data = {"target_url": target, "status": run.status.value}
        return {}
