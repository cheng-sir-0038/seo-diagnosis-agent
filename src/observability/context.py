from __future__ import annotations

from contextvars import ContextVar, Token

from src.observability.models import ObservationContext

_current_context: ContextVar[ObservationContext | None] = ContextVar(
    "agent_observability_context", default=None
)


def get_current_context() -> ObservationContext | None:
    return _current_context.get()


def set_current_context(context: ObservationContext) -> Token:
    return _current_context.set(context)


def reset_current_context(token: Token) -> None:
    _current_context.reset(token)
