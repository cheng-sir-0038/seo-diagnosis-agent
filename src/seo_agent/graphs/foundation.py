from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from src.seo_agent.graphs.observability import LangGraphObserver


class FoundationState(TypedDict):
    thread_id: str
    run_id: str
    site_id: str
    current_stage: str
    events: list[dict]


def build_foundation_graph(
    checkpointer: BaseCheckpointSaver,
    observer: LangGraphObserver | None = None,
):
    async def initialize_foundation(state: FoundationState) -> dict:
        if observer is None:
            events = [*state.get("events", []), {"type": "foundation.ready"}]
            return {"current_stage": "foundation_ready", "events": events}
        async with observer.node_span("initialize_foundation", input_data=state) as span:
            await observer.event(
                "langgraph.node.started",
                {"node_name": "initialize_foundation"},
                span_id=span.span_id,
            )
            events = [*state.get("events", []), {"type": "foundation.ready"}]
            result = {"current_stage": "foundation_ready", "events": events}
            await observer.event(
                "langgraph.node.completed",
                {"node_name": "initialize_foundation"},
                span_id=span.span_id,
            )
            return result

    builder = StateGraph(FoundationState)
    builder.add_node("initialize_foundation", initialize_foundation)
    builder.add_edge(START, "initialize_foundation")
    builder.add_edge("initialize_foundation", END)
    return builder.compile(checkpointer=checkpointer)
