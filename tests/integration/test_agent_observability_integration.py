from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.agent.loop import AgentLoop
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.deepseek import DeepSeekProvider, DeepSeekRequestError
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.tools.registry import build_default_registry
from src.observability.client import ObservabilityClient
from src.observability.context import get_current_context
from src.observability.exporters import OpenTelemetryExporter
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
async def test_observability_start_failure_ends_agent_run(tmp_path: Path, monkeypatch) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    client = ObservabilityClient(database, project_key="seo-agent")
    await client.startup()
    run = RunManager().create_run([{"role": "user", "content": "test"}])

    async def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic observability storage failure")

    monkeypatch.setattr(database, "execute", fail)
    try:
        await AgentLoop(
            FakeModelProvider.text("ok"), build_default_registry(),
            observer=AgentRunObserver(client, operation_name="agent.chat"),
        ).run(run)
        assert run.status.value == "failed"
        assert run.terminal
        assert run.events[-1].type == "run.failed"
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_observability_start_failure_does_not_leak_trace_context(
    tmp_path: Path, monkeypatch
) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    client = ObservabilityClient(database, project_key="seo-agent")
    await client.startup()
    execute = database.execute

    async def fail_start_event(sql, params=()):
        if "INSERT INTO obs_events" in sql and "trace.started" in params:
            raise sqlite3.OperationalError("synthetic observability storage failure")
        await execute(sql, params)

    monkeypatch.setattr(database, "execute", fail_start_event)
    run = RunManager().create_run([{"role": "user", "content": "test"}])
    try:
        await AgentLoop(
            FakeModelProvider.text("ok"), build_default_registry(),
            observer=AgentRunObserver(client, operation_name="agent.chat"),
        ).run(run)
        assert run.status.value == "failed"
        assert run.events[-1].type == "run.failed"
        assert get_current_context() is None
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_agent_records_partial_usage_and_missing_usage(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    client = ObservabilityClient(database, project_key="seo-agent")
    try:
        for chunks in (
            [ModelChunk(text="ok"), ModelChunk(usage={"input_tokens": 100, "output_tokens": 50,
                                                      "reasoning_tokens": 20})],
            [ModelChunk(text="ok")],
        ):
            run = RunManager().create_run([{"role": "user", "content": "test"}])
            provider = FakeModelProvider([chunks])
            await AgentLoop(
                provider, build_default_registry(),
                observer=AgentRunObserver(client, operation_name="agent.chat"),
            ).run(run)
        calls = await database.fetch_all("SELECT * FROM obs_model_calls ORDER BY completed_at")
        assert [row["total_tokens"] for row in calls] == [150, None]
        assert all(row["time_to_first_token_ms"] is not None for row in calls)
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_agent_records_deepseek_http_status(tmp_path: Path) -> None:
    class FailingProvider(FakeModelProvider):
        async def stream(self, messages, tools, *, tool_choice="auto"):
            raise DeepSeekRequestError(429)
            yield  # pragma: no cover

    database = ObservabilityDatabase(tmp_path / "observability.db")
    client = ObservabilityClient(database, project_key="seo-agent")
    run = RunManager().create_run([{"role": "user", "content": "test"}])
    try:
        await AgentLoop(
            FailingProvider([]), build_default_registry(),
            observer=AgentRunObserver(client, operation_name="agent.chat"),
        ).run(run)
        call = await database.fetch_one("SELECT * FROM obs_model_calls")
        assert call is not None
        assert call["status"] == "FAILED" and call["error_code"] == "HTTP_429"
        assert call["total_tokens"] is None
    finally:
        await client.shutdown()


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


@pytest.mark.asyncio
async def test_agent_exports_decisions_reuse_and_stop_reason(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    client = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(
            id="first", name="calculator", arguments={"expression": "1 + 1"}
        )], finish_reason="tool_calls")],
        [ModelChunk(tool_calls=[ToolCall(
            id="second", name="calculator", arguments={"expression": "1 + 1"}
        )], finish_reason="tool_calls")],
        [ModelChunk(text="完成"), ModelChunk(finish_reason="stop")],
    ])
    run = RunManager().create_run([{
        "role": "user", "content": "private-request API_KEY=sk-example-sensitive-123456789"
    }])
    try:
        await AgentLoop(
            provider, build_default_registry(), max_iterations=4,
            observer=AgentRunObserver(client, operation_name="agent.chat"),
        ).run(run)
        spans = memory.get_finished_spans()
        generations = sorted(
            (span for span in spans
             if span.attributes.get("langfuse.observation.type") == "generation"),
            key=lambda span: span.start_time,
        )
        assert [s.attributes["langfuse.observation.metadata.decision"] for s in generations] == [
            "call_tools", "call_tools", "forced_summary"
        ]
        assert generations[0].attributes["langfuse.observation.metadata.selected_tools"] == (
            "calculator",
        )
        assert generations[2].attributes["langfuse.observation.metadata.decision_reason"] == (
            "duplicate_tool_guard"
        )
        root = next(
            s for s in spans if s.attributes.get("agent.observability.entity_type") == "trace"
        )
        assert (
            root.attributes["langfuse.observation.metadata.completion_mode"]
            == "duplicate_tool_guard"
        )
        assert root.attributes["langfuse.observation.metadata.tool_calls"] == 1
        assert root.attributes["langfuse.observation.metadata.iterations"] == 3
        assert root.attributes["langfuse.observation.metadata.reused_tool_calls"] == 1
        calls = [
            s for s in spans if s.attributes.get("agent.observability.entity_type") == "tool_call"
        ]
        assert len(calls) == 1
        assert calls[0].attributes["langfuse.observation.metadata.operation"] == "execute"
        reused = next(s for s in spans if s.name == "calculator.reused")
        assert reused.attributes["langfuse.observation.metadata.reused"] is True
        assert "1 + 1" in reused.attributes["langfuse.observation.input"]
        assert "2" in reused.attributes["langfuse.observation.output"]
        serialized = str([dict(span.attributes) for span in spans])
        assert "private-request" in serialized
        assert "sk-example-sensitive-123456789" not in serialized
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_agent_trace_explains_budget_forced_summary(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    client = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    run = RunManager().create_run([{"role": "user", "content": "计算"}])
    try:
        await AgentLoop(
            FakeModelProvider.calculator_then_text("1 + 1", "完成"), build_default_registry(),
            max_iterations=2, observer=AgentRunObserver(client, operation_name="agent.chat"),
        ).run(run)
        spans = memory.get_finished_spans()
        root = next(
            s for s in spans if s.attributes.get("agent.observability.entity_type") == "trace"
        )
        assert root.attributes["langfuse.observation.metadata.completion_mode"] == "budget_limit"
        assert root.attributes["langfuse.observation.metadata.partial"] is True
        forced = next(
            s for s in spans
            if s.attributes.get("langfuse.observation.metadata.decision") == "forced_summary"
        )
        assert forced.attributes["langfuse.observation.metadata.decision_reason"] == "budget_limit"
        assert forced.attributes["langfuse.observation.metadata.tool_choice"] == "none"
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_model_prompt_and_tool_io_are_recorded_and_credentials_masked(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    client = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    prompt = "long-prompt:" + "x" * 5_000 + " API_KEY=sk-example-sensitive-123456789"
    run = RunManager().create_run([{"role": "user", "content": prompt}])
    try:
        await AgentLoop(
            FakeModelProvider.calculator_then_text("1234 * 56", "结果是 69104。"),
            build_default_registry(),
            observer=AgentRunObserver(client, operation_name="agent.chat"),
        ).run(run)
        spans = memory.get_finished_spans()
        generations = [
            s for s in spans if s.attributes.get("langfuse.observation.type") == "generation"
        ]
        assert len(generations) == 2
        rendered = str([dict(s.attributes) for s in spans])
        assert "x" * 5_000 in rendered
        assert "sk-example-sensitive-123456789" not in rendered
        assert "[REDACTED]" in rendered
        assert "1234 * 56" in rendered
        assert "69104" in rendered
        assert all("langfuse.observation.input" in s.attributes for s in generations)
        assert all("langfuse.observation.output" in s.attributes for s in generations)
        tool = next(
            s for s in spans if s.attributes.get("agent.observability.entity_type") == "tool_call"
        )
        assert "1234 * 56" in tool.attributes["langfuse.observation.input"]
        assert "69104" in tool.attributes["langfuse.observation.output"]
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_agent_unknown_tool_is_marked_but_model_output_is_preserved(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    client = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(
            id="bad", name="private_unknown_tool", arguments={}
        )], finish_reason="tool_calls")],
        [ModelChunk(text="无法执行"), ModelChunk(finish_reason="stop")],
    ])
    run = RunManager().create_run([{"role": "user", "content": "test"}])
    try:
        await AgentLoop(
            provider, build_default_registry(),
            observer=AgentRunObserver(client, operation_name="chat"),
        ).run(run)
        spans = memory.get_finished_spans()
        assert "private_unknown_tool" in str([dict(s.attributes) for s in spans])
        assert any(s.name == "unknown_tool" for s in spans)
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_paused_agent_trace_is_interrupted_not_completed(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    client = ObservabilityClient(database, project_key="seo-agent")
    run = RunManager().create_run([{"role": "user", "content": "test"}])

    class PausingProvider(FakeModelProvider):
        async def stream(self, messages, tools, *, tool_choice="auto"):
            run.request_pause()
            raise asyncio.CancelledError()
            yield  # pragma: no cover

    try:
        with pytest.raises(asyncio.CancelledError):
            await AgentLoop(
                PausingProvider([]), build_default_registry(),
                observer=AgentRunObserver(client, operation_name="agent.chat"),
            ).run(run)
        trace = await database.fetch_one("SELECT status, metadata_json FROM obs_traces")
        assert trace is not None and trace["status"] == "INTERRUPTED"
        run_row = await database.fetch_one("SELECT status FROM obs_runs")
        assert run_row is not None and run_row["status"] == "INTERRUPTED"
        model = await database.fetch_one(
            "SELECT status FROM obs_spans WHERE operation_name = 'model.iteration.1'"
        )
        assert model is not None and model["status"] == "CANCELLED"
        event = await database.fetch_one(
            "SELECT severity FROM obs_events WHERE event_type = 'run.interrupted'"
        )
        assert event is not None and event["severity"] == "INFO"
    finally:
        await client.shutdown()


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


def test_deepseek_parser_does_not_invent_missing_usage() -> None:
    provider = DeepSeekProvider(api_key="test", model="deepseek-chat")
    chunks = provider.parse_payload({"usage": {"prompt_tokens": 12, "completion_tokens": None}})
    assert chunks[0].usage == {"input_tokens": 12}


@pytest.mark.asyncio
async def test_structured_prd_resolves_prompt_version_and_records_model_call(
    tmp_path: Path, approve_opportunity,
) -> None:
    business = SeoAgentDatabase(tmp_path / "seo-agent.db")
    observability = ObservabilityDatabase(tmp_path / "observability.db")
    await business.startup()
    await observability.startup()
    registry = PromptRegistry(observability)
    await registry.startup()
    seeder = SeoPromptSeeder(registry)
    seeded = await seeder.seed()
    memory = InMemorySpanExporter()
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=observability
    )
    observer = ObservabilityClient(observability, project_key="seo-agent", exporters=[exporter])
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
        "'Maintenance Cost', 88, 'HIGH', 'WAITING_FOR_DECISION', 'pricing gap', 'v1', "
        "'2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00', "
        "'2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00')",
        (site["id"],),
    )
    await approve_opportunity(business, site["id"], "opp-1", owner["id"])
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
        messages = json.loads(model_call["input_summary_json"])["messages"]
        assert {message["type"] for message in messages} == {"system", "human"}
        generation = next(
            s for s in memory.get_finished_spans()
            if s.attributes.get("langfuse.observation.type") == "generation"
        )
        assert "maintenance cost" in generation.attributes["langfuse.observation.input"]
        assert "Maintenance Cost" in generation.attributes["langfuse.observation.output"]
    finally:
        await business.shutdown()
        await observer.shutdown()


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
async def test_structured_raw_message_does_not_invent_missing_usage() -> None:
    raw = AIMessage(content="", response_metadata={"token_usage": {"prompt_tokens": 12}})

    class RawModel:
        def with_structured_output(self, schema: Any, **_: Any) -> FakeRunnable:
            return FakeRunnable({"raw": raw, "parsed": {}, "parsing_error": None})

    result = await invoke_structured_model(RawModel(), CandidatePrompt, [])
    assert result.usage == {"input_tokens": 12}


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
        assert "system" in call["input_summary_json"]
        assert "question" in call["input_summary_json"]
        assert issue is not None
        assert issue["issue_type"] == "PROMPT_EVALUATION_MODEL_FAILED"
    finally:
        await database.shutdown()
