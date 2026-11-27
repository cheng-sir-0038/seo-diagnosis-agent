"""Per-run services for registered workspace specialists."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.workspace.registry import ToolContext
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind

if TYPE_CHECKING:
    from src.agent.config import Skill
    from src.agent.models import AgentRun
    from src.agent.runtime.executor import AgentExecutor
    from src.agent.tools.registry import ToolRegistry
    from src.agent.workspace.registry import SpecialistDefinition, WorkspaceResources

SKILL_LOADED_EVENT = "skill.loaded"
LoadDialog = Callable[[str, str, str], Awaitable[list[dict[str, str]]]]
LoadToolData = Callable[[str, tuple[str, ...]], Awaitable[list[dict[str, Any]]]]


def run_dialog(run: AgentRun) -> list[dict[str, str]]:
    """Read the user-visible dialogue without local system or tool messages."""
    return [
        {"role": message["role"], "content": str(message["content"])}
        for message in run.messages
        if message["role"] in {"user", "assistant"} and message.get("content")
        and not message.get("tool_calls")
    ]


async def previous_dialog(run: AgentRun, agent: str | None, load: LoadDialog) -> list[dict[str, str]]:
    saved = run.dispatch_state
    source = saved.get("previous_run_id")
    if not source or not agent or not run.conversation_id or saved.get("previous_agent") != agent:
        return []
    return await load(source, run.conversation_id, agent)


@dataclass(frozen=True)
class SpecialistExecution:
    definition: SpecialistDefinition
    run: AgentRun
    executor: AgentExecutor
    resources: WorkspaceResources
    agent_run_id: str
    load_dialog: LoadDialog
    load_tool_data: LoadToolData | None

    async def tools(self, *, user_messages: tuple[str, ...] = (), **options: Any) -> ToolRegistry:
        """Create the tools for this invocation from the registered factory."""
        return await self.definition.tools(ToolContext(
            self.resources, target_url=self.run.dispatch_state.get("target_url"),
            user_messages=user_messages, options=options,
        ))

    async def dialog(self) -> list[dict[str, str]]:
        """Reuse this specialist's previous dialogue or the current resumed turn."""
        current = run_dialog(self.run)
        if self.run.dispatch_state.get("active_agent"):
            return current
        return [*await previous_dialog(self.run, self.definition.id, self.load_dialog), *current]

    async def record_skill(self, skill: Skill, prompt_hash: str | None = None) -> None:
        """Emit the selected Skill once for the current specialist execution."""
        if any(
            event.type == SKILL_LOADED_EVENT and event.payload.get("agent_run_id") == self.agent_run_id
            for event in self.run.events
        ):
            return
        payload = {
            "agent": self.definition.id, "skill": skill.name, "label": skill.description,
            "tools": self.executor.tools.names(),
            "prompt_hash": prompt_hash or hashlib.sha256(skill.prompt_fragment.encode()).hexdigest(),
        }
        async with observed_operation(
            self.resources.observability, "workspace.skill", kind=SpanKind.INTERNAL,
            input_data=payload,
        ):
            await self.executor.emit(self.run, SKILL_LOADED_EVENT, payload)


async def run_default_specialist(execution: SpecialistExecution) -> None:
    """Run a registered model-and-tools specialist using the common executor."""
    run, executor, definition = execution.run, execution.executor, execution.definition
    async with observed_operation(
        execution.resources.observability, f"workspace.{definition.id}", kind=SpanKind.AGENT,
        metadata={
            "agent": definition.id, "agent_run_id": execution.agent_run_id,
            "agent_profile": definition.profile.public_data(),
        },
    ):
        dialog = await execution.dialog()
        executor.tools = await execution.tools(
            user_messages=tuple(m["content"] for m in dialog if m["role"] == "user"),
        )
        if not run.dispatch_state.get("active_agent"):
            run.messages = [{"role": "system", "content": definition.instructions}, *dialog]
            run.dispatch_state["active_agent"] = definition.id
            await executor.checkpoint(run, stage="specialist_ready")
        await executor.run_turns(run)
