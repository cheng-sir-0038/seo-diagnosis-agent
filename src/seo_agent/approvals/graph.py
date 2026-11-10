from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.seo_agent.graphs.observability import LangGraphObserver


class ApprovalState(TypedDict):
    approval_action_id: str
    current_stage: str
    decision: dict[str, Any] | None


def build_approval_graph(
    checkpointer: BaseCheckpointSaver,
    observer: LangGraphObserver | None = None,
):
    async def wait_for_approval(state: ApprovalState) -> dict[str, Any]:
        if observer is not None:
            async with observer.node_span("wait_for_approval", input_data=state) as span:
                await observer.event(
                    "langgraph.node.started",
                    {"node_name": "wait_for_approval"},
                    span_id=span.span_id,
                )
                await observer.event(
                    "langgraph.interrupt.requested",
                    {
                        "node_name": "wait_for_approval",
                        "approval_action_id": state["approval_action_id"],
                    },
                    stage="waiting_for_approval",
                    span_id=span.span_id,
                )
        decision = interrupt(
            {
                "approval_action_id": state["approval_action_id"],
                "allowed_decisions": ["approve", "edit_and_approve", "reject"],
            }
        )
        if observer is not None:
            await observer.event(
                "langgraph.resumed",
                {
                    "node_name": "wait_for_approval",
                    "approval_action_id": state["approval_action_id"],
                },
                stage="approval_resolved",
            )
        return {"decision": decision, "current_stage": "approval_resolved"}

    builder = StateGraph(ApprovalState)
    builder.add_node("wait_for_approval", wait_for_approval)
    builder.add_edge(START, "wait_for_approval")
    builder.add_edge("wait_for_approval", END)
    return builder.compile(checkpointer=checkpointer)
