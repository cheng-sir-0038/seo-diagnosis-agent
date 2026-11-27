"""Explicit registration of workspace specialists and their public metadata."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, create_model

if TYPE_CHECKING:
    from src.agent.config import Skill
    from src.agent.tools.registry import ToolRegistry
    from src.agent.workspace.execution import SpecialistExecution
    from src.core.cache import CacheManager
    from src.core.config import Settings
    from src.facade.data_facade import DataFacade
    from src.observability.client import ObservabilityClient
    from src.seo_agent.db.database import SeoAgentDatabase
    from src.seo_agent.workflows.structured_model import StructuredModelRunner

SUPERVISOR_NODE = "supervisor"
CLARIFY = "clarify"
COURSE_REVIEW = "course_review"
_SYSTEM_IDS = frozenset({SUPERVISOR_NODE, CLARIFY, COURSE_REVIEW, "start", "end", "run", "next_node"})
_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")


class SpecialistUnavailable(RuntimeError):
    """A saved run refers to a specialist or handoff no longer registered."""

    def __init__(self) -> None:
        super().__init__("此任务原来的专员当前不可用。已有结果仍可查看，也可以新建对话。")


class SpecialistContextUnavailable(ValueError):
    """A directory context cannot build a specialist's tools."""


class RouteDecision(BaseModel):
    """Supervisor fields shared by all registered routes."""

    model_config = ConfigDict(extra="forbid")
    route: str
    needs_page: bool = Field(description="需要指定页面或站点才可执行时为 true")
    target_index: int | None = Field(default=None, ge=1, description="候选页面从 1 开始的序号")
    question: str | None = Field(default=None, max_length=300, description="意图不清时的追问")


@dataclass(frozen=True)
class SpecialistProfile:
    name: str
    icon: str = "profile"
    tone: str = "indigo"
    directory_tone: str = "learning"

    def public_data(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PublicSkill:
    name: str
    label: str
    summary: str
    tools: tuple[str, ...]
    instruction: str
    usage: Mapping[str, tuple[str, str, str, str]]

    @classmethod
    def publish(
        cls, skill: Skill, *, summary: str, instruction: str,
        usage: Mapping[str, tuple[str, str, str, str]],
    ) -> PublicSkill:
        """Opt in authored static text for this Skill and only its own tool rows."""
        return cls(
            str(skill.name), skill.description, summary, tuple(skill.tools), instruction,
            MappingProxyType({name: usage[name] for name in skill.tools if name in usage}),
        )


@dataclass(frozen=True)
class PublicInstruction:
    id: str
    label: str
    text: str
    tools: tuple[str, ...] | None = None
    attach_to_tools: bool = True


@dataclass(frozen=True)
class WorkspaceResources:
    """Application services shared by tool factories. Per-run data belongs in ToolContext."""

    settings: Settings
    database: SeoAgentDatabase
    facade: DataFacade
    cache: CacheManager
    planner: StructuredModelRunner
    observability: ObservabilityClient | None = None
    find_site: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None


@dataclass(frozen=True)
class ToolContext:
    resources: WorkspaceResources
    target_url: str | None = None
    site: dict[str, Any] | None = None
    user_messages: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingTurn:
    """The latest intent and supervisor state offered to a domain's intake policy."""

    decision: RouteDecision
    state: dict[str, Any]
    latest: str
    pending_route: str | None
    candidates: list[str]
    invalid_target: bool
    answered: bool


@dataclass(frozen=True)
class SpecialistDefinition:
    """Stable identity, fresh tool factory, execution and explicitly public material."""

    id: str
    profile: SpecialistProfile
    routing_prompt: str
    tools: Callable[[ToolContext], Awaitable[ToolRegistry]]
    execute: Callable[[SpecialistExecution], Awaitable[None]] | None = None
    instructions: str = ""
    intake: type[BaseModel] | None = None
    prepare: Callable[[RoutingTurn], str | None] | None = None
    inherit: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    routing_context: Callable[[dict[str, Any], str | None], dict[str, Any]] | None = None
    shortcut: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None
    public_skills: tuple[PublicSkill, ...] = ()
    public_instructions: tuple[PublicInstruction, ...] = ()
    deterministic_path: str | None = None
    directory_note: str | None = None
    no_target_note: str | None = None
    requires_text_model: bool = True


class SpecialistRegistry:
    """Register definitions by ID once and derive the executable routing contract."""

    def __init__(
        self, definitions: Iterable[SpecialistDefinition], *,
        handoffs: Mapping[str, tuple[str, str]] | None = None,
    ) -> None:
        by_id: dict[str, SpecialistDefinition] = {}
        fields = {}
        for definition in definitions:
            key = definition.id
            if key in _SYSTEM_IDS or not _ID.fullmatch(key):
                raise ValueError(f"Invalid or reserved specialist ID: {key}")
            if key in by_id:
                raise ValueError(f"Duplicate specialist ID: {key}")
            if not definition.profile.name.strip() or not definition.routing_prompt.strip():
                raise ValueError(f"Missing specialist name or routing instructions: {key}")
            if definition.execute is None and not definition.instructions.strip():
                raise ValueError(f"Missing runtime instructions: {key}")
            public_ids = [skill.name for skill in definition.public_skills]
            public_ids.extend(prompt.id for prompt in definition.public_instructions)
            if len(set(public_ids)) != len(public_ids):
                raise ValueError(f"Duplicate public instruction ID: {key}")
            if definition.intake is not None:
                for name, spec in definition.intake.model_fields.items():
                    if name in RouteDecision.model_fields or name in fields:
                        raise ValueError(f"Duplicate route field: {name}")
                    if spec.is_required():
                        raise ValueError(f"Route field must be optional for other specialists: {name}")
                    fields[name] = (spec.rebuild_annotation(), deepcopy(spec))
            by_id[key] = definition
        self._definitions = MappingProxyType(by_id)
        self.handoffs = MappingProxyType(dict(handoffs or {}))
        for route, (source, target) in self.handoffs.items():
            if route != COURSE_REVIEW or source == target or source not in by_id or target not in by_id:
                raise ValueError(f"Invalid handoff {route}: {source} -> {target}")
        routes = [*by_id, *self.handoffs, CLARIFY]
        # Constrain the model's JSON schema and check membership explicitly before
        # dispatch. This lets the supervisor clarify an unknown model choice.
        self.route_model = create_model(
            "WorkspaceRouteDecision", __base__=RouteDecision,
            route=(str, Field(json_schema_extra={"enum": routes})), **fields,
        )

    def get(self, specialist_id: str) -> SpecialistDefinition:
        """Resolve the stable ID or reject an unregistered specialist."""
        return self._definitions[specialist_id]

    def find(self, specialist_id: object) -> SpecialistDefinition | None:
        return self._definitions.get(specialist_id) if isinstance(specialist_id, str) else None

    def accepts_route(self, route: object) -> bool:
        return isinstance(route, str) and (
            route == CLARIFY or route in self._definitions or route in self.handoffs
        )

    def check_saved_route(self, state: Mapping[str, Any]) -> None:
        """Reject unavailable saved routes without replacing their prior evidence."""
        route, active = state.get("route"), state.get("active_agent")
        if (route and not self.accepts_route(route)) or (active and self.find(active) is None):
            raise SpecialistUnavailable()

    def __iter__(self):
        return iter(self._definitions.values())

    def routing_instructions(self) -> str:
        return "\n".join(
            f"{definition.id}（{definition.profile.name}）：{definition.routing_prompt}"
            for definition in self
        )
