"""Dispatch workspace runs through the registered LangGraph nodes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from src.agent.models import AgentRun, HumanInputState
from src.agent.runtime.executor import AgentExecutor
from src.agent.workspace.execution import (
    LoadDialog, LoadToolData, SpecialistExecution, previous_dialog, run_default_specialist, run_dialog,
)
from src.agent.workspace.registry import (
    CLARIFY, COURSE_REVIEW, SUPERVISOR_NODE, RoutingTurn, SpecialistDefinition,
    SpecialistRegistry, WorkspaceResources,
)
from src.agent.workspace.routing import is_bare_page_reply, page_candidates, routing_prompt, select_page
from src.healthcare_agent.specialist import collect_public_syllabus
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind

_SUPERVISOR_INPUT = "workspace_clarification"


class GraphState(TypedDict):
    """Carry the app run and the next graph edge for one invocation."""

    run: AgentRun
    next_node: str


def conversation_context(
    previous: dict[str, Any], specialists: SpecialistRegistry | None = None,
) -> dict[str, Any]:
    """Collect routing context from a completed run without loading its dialogue."""
    if specialists is None:
        from src.agent.workspace.bootstrap import workspace_specialists

        specialists = workspace_specialists
    context = {
        "target_url": previous.get("target_url"),
        "previous_route": previous.get("route"),
        "previous_run_id": previous.get("source_run_id"),
        "previous_agent": previous.get("active_agent"),
    }
    for definition in specialists:
        if definition.inherit:
            context.update(definition.inherit(previous))
    return context


class WorkspaceTeam:
    """Run the supervisor, clarification and specialist nodes within one app Run."""

    def __init__(
        self, executor: AgentExecutor, specialists: SpecialistRegistry, resources: WorkspaceResources,
        load_dialog: LoadDialog, load_tool_data: LoadToolData | None,
    ) -> None:
        self.executor = executor
        self.registry, self.resources = specialists, resources
        self.load_dialog, self.load_tool_data = load_dialog, load_tool_data
        self.prompt_key, self.prompt_definition = routing_prompt(specialists)
        self.course_handoff = specialists.handoffs.get(COURSE_REVIEW)
        graph = StateGraph(GraphState)
        graph.add_node(SUPERVISOR_NODE, self.plan)
        graph.add_node(CLARIFY, self.clarify)
        for definition in specialists:
            node = definition.id
            graph.add_node(node, self.registered_node(definition))
            if self.course_handoff and node == self.course_handoff[0]:
                target = self.course_handoff[1]
                graph.add_conditional_edges(node, self._next_node, {target: target, END: END})
            else:
                graph.add_edge(node, END)
        graph.add_edge(START, SUPERVISOR_NODE)
        graph.add_conditional_edges(SUPERVISOR_NODE, self._next_node, {
            node: node for node in (CLARIFY, *(definition.id for definition in specialists))
        })
        graph.add_edge(CLARIFY, END)
        self.graph = graph.compile()

    @staticmethod
    def _next_node(state: GraphState) -> str:
        return state["next_node"]

    async def execute(self, run: AgentRun) -> None:
        """Enter the team graph with a fresh or restored run from the executor."""
        await self.graph.ainvoke({"run": run, "next_node": SUPERVISOR_NODE})

    async def plan(self, state: GraphState) -> dict[str, str]:
        """Checkpoint a validated route, or reuse the route already saved on resume."""
        run = state["run"]
        saved = run.dispatch_state
        self.registry.check_saved_route(saved)
        receipt = run.human_input
        answered = bool(receipt and receipt.tool_name == _SUPERVISOR_INPUT and receipt.answer)
        if not saved.get("route") or answered:
            dialog = run_dialog(run)
            latest = next(m["content"] for m in reversed(dialog) if m["role"] == "user")
            targets, invalid = page_candidates(latest)
            pending_route = saved.get("route") if answered else None
            bare_page_answer = bool(
                pending_route and saved.get("awaiting_page") and len(targets) == 1
                and not invalid and is_bare_page_reply(latest)
            )
            if answered and not targets and not invalid:
                targets = saved.get("pending_targets", [])
            async with observed_operation(
                self.resources.observability, "workspace.supervisor", kind=SpanKind.AGENT,
                input_data={"request": latest, "target_candidates": targets},
            ) as span:
                schema = self.registry.route_model
                previous_definition = self.registry.find(saved.get("previous_route"))
                shortcut = (
                    previous_definition.shortcut(latest, saved)
                    if not answered and previous_definition and previous_definition.shortcut else None
                )
                if bare_page_answer:
                    decision = schema(route=pending_route, needs_page=True, target_index=1)
                elif shortcut:
                    decision = schema.model_validate(shortcut)
                else:
                    previous = await previous_dialog(run, saved.get("previous_agent"), self.load_dialog)
                    context = {
                        "current_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                        "conversation": [{**m, "content": m["content"][-2000:]} for m in dialog[-6:]],
                        "previous_route": pending_route or saved.get("previous_route"),
                        "pending_intent": saved.get("intent") if answered else None,
                        "awaiting_page": bool(answered and saved.get("awaiting_page")),
                        "previous_intent": next(
                            (m["content"] for m in reversed(previous) if m["role"] == "user"), None,
                        ),
                        "current_page": saved.get("target_url"),
                        "candidate_pages": targets,
                    }
                    for definition in self.registry:
                        if definition.routing_context:
                            context.update(definition.routing_context(saved, pending_route))
                    result = await self.resources.planner.invoke(
                        prompt_key=self.prompt_key, prompt_definition=self.prompt_definition,
                        schema=schema, method="json_mode", context=context,
                        operation="workspace.route", business_object={"run_id": run.id},
                        application_run=run,
                    )
                    decision = schema.model_validate(result.output)
                    for key, count in result.usage.items():
                        run.token_usage[key] = run.token_usage.get(key, 0) + count

                if not self.registry.accepts_route(decision.route):
                    decision = schema(
                        route=CLARIFY, needs_page=False,
                        question="这次未能匹配到可用专员。请换一种说法，说明你想得到什么结果。",
                    )
                saved["awaiting_page"] = False
                turn = RoutingTurn(decision, saved, latest, pending_route, targets, invalid, answered)
                question = None
                if decision.route == COURSE_REVIEW:
                    question = self._prepare_course_review(turn, bare_page_answer)
                else:
                    saved.pop("course_review_requests", None)
                    if decision.route == CLARIFY:
                        question = decision.question or "你想完成什么任务？请补充希望得到的结果。"
                    elif (definition := self.registry.get(decision.route)).prepare:
                        question = definition.prepare(turn)
                saved.update(
                    route=decision.route, question=question,
                    pending_targets=targets if saved["awaiting_page"] else [],
                )
                if not answered or decision.route != pending_route or not saved.get("intent"):
                    saved["intent"] = latest[:2000]
                if answered and receipt:
                    await self.executor.emit(run, "input.accepted", {
                        "request_id": receipt.request_id, "answer": receipt.answer, "action": receipt.action,
                    })
                    run.human_input = None
                await self.executor.checkpoint(run, stage="route_selected")
                if span:
                    span.output_data = {
                        "route": decision.route, "target_url": saved.get("target_url"), "question": question,
                    }

        if saved.get("question") or saved["route"] == CLARIFY:
            return {"next_node": CLARIFY}
        if saved["route"] == COURSE_REVIEW and self.course_handoff:
            source, target = self.course_handoff
            return {"next_node": target if saved.get("public_rule") else source}
        return {"next_node": saved["route"]}

    @staticmethod
    def _prepare_course_review(turn: RoutingTurn, bare_page_answer: bool) -> str | None:
        """Keep only the current course task and the confirmed page for the handoff."""
        saved = turn.state
        saved["target_url"], question = select_page(
            saved.get("target_url"), turn.candidates, turn.invalid_target, turn.decision,
        )
        saved["awaiting_page"] = bool(question)
        if turn.pending_route == COURSE_REVIEW:
            requests = saved.get("course_review_requests") or ([saved["intent"]] if saved.get("intent") else [])
            if not bare_page_answer:
                requests = [*requests, turn.latest]
        else:
            requests = [turn.latest]
        saved["course_review_requests"] = requests
        return question

    async def clarify(self, state: GraphState) -> dict:
        """Wait with a persistent input request ID and without granting tools."""
        run = state["run"]
        if run.human_input is None:
            run.human_input = HumanInputState(
                questions=[run.dispatch_state.get("question") or "请说明你想完成的任务。"],
                request_id=f"input_{uuid4().hex}", tool_name=_SUPERVISOR_INPUT,
                tool_arguments={}, tool_result={},
            )
        await self.executor.run_turns(run)
        return {}

    def registered_node(self, definition: SpecialistDefinition):
        """Bind the definition to a fresh execution context inside its lifecycle."""
        agent = definition.id
        execute_specialist = definition.execute or run_default_specialist

        async def execute(state: GraphState) -> dict[str, str]:
            run = state["run"]
            async with self.executor.specialist(
                run, agent, sender=SUPERVISOR_NODE, profile=definition.profile.public_data(),
            ) as agent_run_id:
                execution = SpecialistExecution(
                    definition, run, self.executor, self.resources, agent_run_id,
                    self.load_dialog, self.load_tool_data,
                )
                if (
                    self.course_handoff and agent == self.course_handoff[0]
                    and run.dispatch_state["route"] == COURSE_REVIEW
                ):
                    evidence = await collect_public_syllabus(execution)
                    run.dispatch_state["public_rule"] = evidence
                    await self.executor.complete_specialist(run)
                    await self.executor.checkpoint(run, stage="public_rule_verified")
                    return {"next_node": self.course_handoff[1]}
                await execute_specialist(execution)
            return {"next_node": END}

        return execute
