from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from src.observability.client import ObservabilityClient
from src.observability.models import SpanKind

P = ParamSpec("P")
R = TypeVar("R")


def observed_operation(
    client: ObservabilityClient,
    operation_name: str,
    *,
    trigger_type: str = "decorator",
    span_kind: SpanKind = SpanKind.INTERNAL,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            async with client.trace(
                operation_name, input_data={"args": args, "kwargs": kwargs}
            ) as trace:
                async with trace.run(trigger_type=trigger_type) as run:
                    async with run.span(operation_name, kind=span_kind):
                        return await func(*args, **kwargs)

        return wrapper

    return decorator
