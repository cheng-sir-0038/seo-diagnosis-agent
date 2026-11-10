from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent.loop import AgentLoop
from src.agent.observability import AgentRunObserver
from src.agent.providers.deepseek import DeepSeekProvider
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.tools.registry import build_default_registry
from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.promptops.models import CandidatePrompt
from src.promptops.registry import PromptRegistry
from src.seo_agent.approvals.graph import build_approval_graph
from src.seo_agent.automation.model_result import invoke_structured_model
from src.seo_agent.automation.models import DeliveryReviewOutput
from src.seo_agent.automation.prompt_optimization import (
    DeepSeekCandidateGenerator,
    DeepSeekPromptExecutor,
)
from src.seo_agent.automation.prompts import SeoPromptSeeder
from src.seo_agent.automation.structured import StructuredModelAutomation
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.graphs.observability import LangGraphObserver
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService


class FakeRunnable:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def ainvoke(self, _: Any) -> Any:
        return self.value


class FailingRunnable:
    async def ainvoke(self, _: Any) -> Any:
        raise TimeoutError("model timeout")


class FakeStructuredModel:
    def __init__(self, responses: dict[type, Any]) -> None:
        self.responses = responses

    def with_structured_output(self, schema: type, **_: Any) -> FakeRunnable:
        return FakeRunnable(self.responses[schema])


class RoutingStructuredModel:
    provider_name = "fake"
    model_name = "fake-structured"

    def with_structured_output(self, schema: Any, **_: Any) -> FakeRunnable:
        if schema is CandidatePrompt:
            return FakeRunnable(
                CandidatePrompt(
                    system_template="strict evidence required",
                    user_template="{{input}}",
                    output_schema={"required": ["answer", "evidence"]},
                    change_summary="补充证据约束",
                )
            )
        return FakeRunnable({"answer": "maintenance cost", "evidence": ["source-1"]})


class FailingStructuredModel:
    provider_name = "fake"
    model_name = "fake-failing"

    def with_structured_output(self, schema: Any, **_: Any) -> FailingRunnable:
        return FailingRunnable()


@pytest.mark.asyncio
async def test_agent_loop_persists_model_tool_events_and_sanitizes_arguments(
    tmp_path: Path,
) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    client = ObservabilityClient(database, project_key="seo-agent")
    manager = RunManager()
    run = manager.create_run([{"role": "user", "content": "1234 * 56"}])
    provider = FakeModelProvider.calculator_then_text("1234 * 56", "结果是 69104。")
    observer = AgentRunObserver(
        client,
        operation_name="agent.chat",
        session_id="conversation-1",
        agent_name="chat-agent",
    )
    try:
        await AgentLoop(
            provider,
            build_default_registry(),
            observer=observer,
        ).run(run)
        counts = await database.table_counts()
        assert counts["obs_traces"] == 1
        assert counts["obs_runs"] == 1
        assert counts["obs_model_calls"] == 2
        assert counts["obs_tool_calls"] == 1
        event_types = {
            row["event_type"]
            for row in await database.fetch_all("SELECT event_type FROM obs_events")
        }
        assert {"run.started", "tool.started", "tool.completed", "run.completed"}.issubset(
            event_types
        )
        assert (
            await database.fetch_value(
                "SELECT COUNT(*) FROM obs_events WHERE event_type = 'run.started'"
            )
            == 1
        )
        assert (
            await database.fetch_value(
                "SELECT COUNT(*) FROM obs_events WHERE event_type = 'run.completed'"
            )
            == 1
        )
        trace = (await client.list_traces())[0]
        detail = await client.get_trace(trace["id"])
        assert detail["status"] == "COMPLETED"
        assert detail["tool_calls"][0]["tool_name"] == "calculator"
    finally:
        await database.shutdown()


def test_deepseek_parser_emits_usage_chunk() -> None:
    provider = DeepSeekProvider(api_key="test", model="deepseek-chat")
    chunks = provider.parse_payload(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 101,
                "completion_tokens": 23,
                "total_tokens": 124,
                "prompt_cache_hit_tokens": 20,
            },
        }
    )
    assert chunks[0].usage["input_tokens"] == 101
    assert chunks[0].usage["output_tokens"] == 23
    assert chunks[0].usage["cached_tokens"] == 20


@pytest.mark.asyncio
async def test_structured_prd_resolves_prompt_version_and_records_model_call(
    tmp_path: Path,
) -> None:
    business = SeoAgentDatabase(tmp_path / "seo-agent.db")
    observability = ObservabilityDatabase(tmp_path / "observability.db")
    await business.startup()
    await observability.startup()
    registry = PromptRegistry(observability)
    await registry.startup()
    seeder = SeoPromptSeeder(registry)
    seeded = await seeder.seed()
    observer = ObservabilityClient(observability, project_key="seo-agent")
    system = SeoAgentSystemService(business)
    site = await system.create_site(
        name="Demo",
        domain="demo.test",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@test.com")
    await business.execute(
        "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, opportunity_type, "
        "target_country, target_language, title, current_score, priority_level, status, "
        "current_summary, policy_version, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES ('opp-1', ?, 'fp', 'maintenance cost', 'CONTENT_GAP', 'US', 'en', "
        "'Maintenance Cost', 88, 'HIGH', 'APPROVED', 'pricing gap', 'v1', "
        "'2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00', "
        "'2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00')",
        (site["id"],),
    )
    tasks = TaskDeliveryService(business, artifact_root=tmp_path / "artifacts")
    task = await tasks.create_task(
        site_id=site["id"],
        title="Maintenance guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=["opp-1"],
        owner_actor_id=owner["id"],
    )
    prd = TaskPrdContent(
        background="pricing gap",
        target_intent="maintenance cost",
        target_keywords=["maintenance cost"],
        target_audience="buyers",
        competitor_gap="missing pricing",
        content_type="article",
        content_outline=["cost factors"],
        seo_title="Maintenance Cost",
        seo_description="Maintenance cost guide",
        acceptance_criteria=["covers cost factors"],
    )
    model = FakeStructuredModel(
        {
            TaskPrdContent: prd,
            DeliveryReviewOutput: DeliveryReviewOutput(
                status="ACCEPTED",
                score=90,
                passed_items=[],
                failed_items=[],
                missing_items=[],
                revision_instructions=[],
            ),
        }
    )
    automation = StructuredModelAutomation(
        business,
        tasks,
        model_factory=lambda: model,
        prompt_registry=registry,
        observability=observer,
    )
    try:
        result = await automation.generate_prd(
            task_id=task["id"],
            created_by_actor_id=owner["id"],
        )
        assert result["version_no"] == 1
        model_call = await observability.fetch_one("SELECT * FROM obs_model_calls")
        assert model_call is not None
        assert model_call["prompt_version_id"] == seeded["seo.prd.generate"]["id"]
        assert model_call["prompt_key"] == "seo.prd.generate"
    finally:
        await business.shutdown()
        await observability.shutdown()


@pytest.mark.asyncio
async def test_langgraph_observer_records_node_interrupt_and_resume(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    client = ObservabilityClient(database, project_key="seo-agent")
    saver = InMemorySaver()
    try:
        async with client.trace("approval.workflow") as trace:
            async with trace.run(trigger_type="approval", thread_id="thread-1") as run:
                observer = LangGraphObserver(client, trace_id=trace.trace_id, run_id=run.run_id)
                graph = build_approval_graph(saver, observer=observer)
                config = {"configurable": {"thread_id": "thread-1"}}
                initial = await graph.ainvoke(
                    {
                        "approval_action_id": "approval-1",
                        "current_stage": "waiting",
                        "decision": None,
                    },
                    config=config,
                )
                assert initial["__interrupt__"]
                resolved = await graph.ainvoke(
                    Command(resume={"decision": "approve"}),
                    config=config,
                )
                assert resolved["current_stage"] == "approval_resolved"
        event_types = {
            row["event_type"]
            for row in await database.fetch_all("SELECT event_type FROM obs_events")
        }
        assert "langgraph.node.started" in event_types
        assert "langgraph.interrupt.requested" in event_types
        assert "langgraph.resumed" in event_types
        spans = await database.fetch_all("SELECT * FROM obs_spans WHERE kind = 'LANGGRAPH_NODE'")
        assert spans and spans[0]["operation_name"] == "wait_for_approval"
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_production_prompt_optimizer_generator_and_executor_are_observed(
    tmp_path: Path,
) -> None:
    database = ObservabilityDatabase(tmp_path / "prompt-optimization.db")
    await database.startup()
    registry = PromptRegistry(database)
    await registry.startup()
    await SeoPromptSeeder(registry).seed()
    observer = ObservabilityClient(database, project_key="seo-agent")
    prompt = await registry.create_prompt(
        prompt_key="seo.test.optimize",
        name="Test Prompt",
        purpose="Regression test",
    )
    current = await registry.create_version(
        prompt_id=prompt["id"],
        system_template="answer from evidence",
        user_template="{{input}}",
        output_schema={"required": ["answer", "evidence"]},
    )
    await registry.promote(
        prompt["id"],
        current["id"],
        environment="production",
    )
    dataset = await registry.create_dataset(
        name="Regression",
        prompt_id=prompt["id"],
    )
    case = await registry.add_case(
        dataset_id=dataset["id"],
        name="Evidence required",
        input_data={"question": "maintenance cost"},
        expected_output={"answer": "maintenance cost"},
        grading_rules={"required_paths": ["answer", "evidence"]},
    )
    model = RoutingStructuredModel()
    generator = DeepSeekCandidateGenerator(
        model_factory=lambda: model,
        prompt_registry=registry,
        observability=observer,
    )
    executor = DeepSeekPromptExecutor(
        model_factory=lambda: model,
        observability=observer,
    )
    try:
        candidate = await generator.generate(
            current_version=current,
            failures=[{"issue": "missing evidence"}],
        )
        execution = await executor.execute(current, case)
        assert candidate.change_summary == "补充证据约束"
        assert execution.error_message is None
        assert execution.output["evidence"] == ["source-1"]
        calls = await database.fetch_all("SELECT * FROM obs_model_calls ORDER BY started_at")
        assert len(calls) == 2
        assert calls[0]["prompt_key"] == "agent.prompt.optimize"
        assert calls[1]["prompt_version_id"] == current["id"]
        assert calls[1]["evaluation_case_id"] == case["id"]
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_structured_raw_message_usage_is_extracted() -> None:
    raw = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_token_details": {"cache_read": 24},
            "output_token_details": {"reasoning": 10},
        },
        response_metadata={"finish_reason": "stop"},
    )

    class RawModel:
        def with_structured_output(self, schema: Any, **_: Any) -> FakeRunnable:
            return FakeRunnable(
                {
                    "raw": raw,
                    "parsed": CandidatePrompt(
                        system_template="strict",
                        user_template="{{input}}",
                        change_summary="test",
                    ),
                    "parsing_error": None,
                }
            )

    result = await invoke_structured_model(
        RawModel(),
        CandidatePrompt,
        [],
    )
    assert result.usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cached_tokens": 24,
        "reasoning_tokens": 10,
    }
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_prompt_evaluation_failure_records_model_call_and_issue(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "prompt-evaluation-failure.db")
    await database.startup()
    registry = PromptRegistry(database)
    await registry.startup()
    observer = ObservabilityClient(database, project_key="seo-agent")
    prompt = await registry.create_prompt(
        prompt_key="seo.test.failure",
        name="Failure Prompt",
        purpose="Failure observation",
    )
    version = await registry.create_version(
        prompt_id=prompt["id"],
        system_template="system",
        user_template="{{input}}",
        output_schema={"required": ["answer"]},
    )
    dataset = await registry.create_dataset(name="Failure", prompt_id=prompt["id"])
    case = await registry.add_case(
        dataset_id=dataset["id"],
        name="Timeout",
        input_data={"question": "test"},
    )
    executor = DeepSeekPromptExecutor(
        model_factory=FailingStructuredModel,
        observability=observer,
    )
    try:
        result = await executor.execute(version, case)
        assert result.error_message == "model timeout"
        call = await database.fetch_one("SELECT * FROM obs_model_calls")
        issue = await database.fetch_one("SELECT * FROM obs_issues")
        assert call is not None and call["status"] == "FAILED"
        assert call["prompt_version_id"] == version["id"]
        assert call["evaluation_case_id"] == case["id"]
        assert issue is not None
        assert issue["issue_type"] == "PROMPT_EVALUATION_MODEL_FAILED"
    finally:
        await database.shutdown()
