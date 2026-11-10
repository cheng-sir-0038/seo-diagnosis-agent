from __future__ import annotations

from pydantic import BaseModel, Field

from src.agent.models import AgentRun


class TrajectoryCase(BaseModel):
    case_id: str
    user_input: str
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    forbidden_available_tools: list[str] = Field(default_factory=list)
    ordered_tools: list[str] = Field(default_factory=list)
    terminal_status: str = "completed"


class TrajectoryEvaluation(BaseModel):
    case_id: str
    passed: bool
    failures: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    terminal_status: str


def evaluate_trajectory(
    case: TrajectoryCase,
    *,
    tools: list[str],
    terminal_status: str,
    available_tools: set[str] | None = None,
) -> TrajectoryEvaluation:
    failures: list[str] = []
    for required in case.required_tools:
        if required not in tools:
            failures.append(f"缺少必需工具: {required}")
    for forbidden in case.forbidden_tools:
        if forbidden in tools:
            failures.append(f"调用了禁止工具: {forbidden}")
    for forbidden in case.forbidden_available_tools:
        if available_tools is not None and forbidden in available_tools:
            failures.append(f"禁止工具出现在可用工具列表: {forbidden}")
    if case.ordered_tools and tools != case.ordered_tools:
        failures.append(f"工具顺序不符合预期: expected={case.ordered_tools}, actual={tools}")
    if terminal_status != case.terminal_status:
        failures.append(
            f"终态不符合预期: expected={case.terminal_status}, actual={terminal_status}"
        )
    return TrajectoryEvaluation(
        case_id=case.case_id,
        passed=not failures,
        failures=failures,
        tools=list(tools),
        terminal_status=terminal_status,
    )


def evaluate_agent_run(
    case: TrajectoryCase,
    run: AgentRun,
    *,
    available_tools: set[str] | None = None,
) -> TrajectoryEvaluation:
    """Compare the recorded Agent events, not the expected trajectory, with a case."""
    tools = [
        str(event.payload.get("name") or "")
        for event in run.events if event.type == "tool.started"
    ]
    result = evaluate_trajectory(
        case, tools=tools, terminal_status=run.status.value, available_tools=available_tools
    )
    question = next(
        (
            message.get("content") for message in reversed(run.messages)
            if message.get("role") == "user"
        ),
        None,
    )
    if question != case.user_input:
        result.failures.append("运行问题与评测用例不一致")
    for event in run.events:
        if event.type == "tool.failed":
            result.failures.append(f"工具执行失败: {event.payload.get('name') or 'unknown'}")
    if run.partial:
        result.failures.append("运行结果不完整")
    result.passed = not result.failures
    return result
