from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.cache import CacheManager
from src.core.exceptions import InternalDataNotAvailableError, InternalProviderResponseError
from src.models.common import Capability
from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.promptops.registry import PromptRegistry
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.automation.prompts import SeoPromptSeeder
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.opportunities.service import OpportunityService, ScanRunService
from src.seo_agent.schedules.service import WorkScheduleService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.service import TaskDeliveryService
from src.seo_agent.workflows.opportunity_discovery import (
    OpportunityDiscoveryRequest,
    OpportunityDiscoveryWorkflow,
)
from src.seo_agent.workflows.structured_model import StructuredModelRunner


class ResearchData:
    def __init__(self):
        self.calls = []

    def live_capabilities(self):
        return {Capability.AUTOCOMPLETE, Capability.KEYWORD_OVERVIEW, Capability.SERP_OVERVIEW}

    async def query_live(self, **kwargs):
        self.calls.append(kwargs)
        capability = kwargs["capability"]
        data = {
            Capability.AUTOCOMPLETE: [{"value": "waterjet cost", "position": 1}],
            Capability.KEYWORD_OVERVIEW: [
                {"keyword": "waterjet", "search_volume": 100, "keyword_difficulty": 30},
                {"keyword": "waterjet cost", "search_volume": None, "keyword_difficulty": None},
            ],
            Capability.SERP_OVERVIEW: [
                {
                    "rank": 1,
                    "title": "Waterjet guide",
                    "url": "https://competitor.example/guide",
                    "type": "organic",
                }
            ],
        }[capability]
        return {"meta": {"source": "dataforseo", "capability": capability.value}, "data": data}


class CandidateModel:
    provider_name = "fake"
    model = "candidate-fixture"

    def __init__(self):
        self.calls = 0
        self.mode = "valid"
        self.count = 1
        self.score = 90

    def with_structured_output(self, schema, **kwargs):
        model = self

        class Runnable:
            async def ainvoke(self, messages):
                model.calls += 1
                await asyncio.sleep(0)
                context = json.loads(messages[-1].content)
                if model.mode == "upstream_failure":
                    raise RuntimeError("private model provider error")
                if model.mode == "invalid_schema":
                    return schema.model_validate({})
                candidates = []
                for index in range(model.count):
                    candidate = {
                        "normalized_intent": ["waterjet", "waterjet cost"][index],
                        "opportunity_type": "CONTENT_GAP",
                        "target_country": "US",
                        "target_language": "en",
                        "title": "Waterjet opportunity",
                        "summary": "Investigate demand using the supplied evidence.",
                        "keywords": [["waterjet"], ["waterjet cost"]][index],
                        "competitor_pages": ["https://competitor.example/guide"],
                        "evidence_snapshot_ids": [item["id"] for item in context["evidence"]],
                        "score_factors": dict.fromkeys(
                            [
                                "search_demand",
                                "business_intent",
                                "content_gap",
                                "difficulty_fit",
                                "site_fit",
                                "evidence_confidence",
                            ],
                            model.score,
                        ),
                        "search_volume": 999,
                        "keyword_difficulty": 1,
                    }
                    if model.mode == "foreign_evidence":
                        candidate["evidence_snapshot_ids"] = ["not-owned-snapshot"]
                    elif model.mode == "foreign_market":
                        candidate["target_country"] = "GB"
                    elif model.mode == "invented_keyword":
                        candidate["keywords"] = ["not-observed"]
                    elif model.mode == "invented_page":
                        candidate["competitor_pages"] = ["https://invented.example/"]
                    elif model.mode == "search_redirect":
                        candidate["competitor_pages"] = ["https://google.com/goto?url=opaque"]
                    elif model.mode == "wrong_support":
                        candidate["evidence_snapshot_ids"] = [context["evidence"][0]["id"]]
                    candidates.append(candidate)
                return schema.model_validate({"candidates": candidates})

        return Runnable()


@pytest.fixture
async def context(tmp_path):
    database = SeoAgentDatabase(tmp_path / "business.db")
    await database.startup()
    snapshots = CacheManager(tmp_path / "evidence.db")
    await snapshots.startup()
    system = SeoAgentSystemService(database)
    site = await system.create_site(
        name="Demo",
        domain="example.com",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    actor = await system.create_actor(actor_type="USER", name="Owner")
    schedule = await WorkScheduleService(database).create_schedule(
        site_id=site["id"],
        schedule_type="OPPORTUNITY_SCAN",
        name="Scan",
        frequency="DAILY",
        timezone="UTC",
    )
    scan = await ScanRunService(database).start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan-1"
    )
    approvals = ApprovalService(database)
    notifications = NotificationService(database)
    await notifications.seed_event_types()
    opportunities = OpportunityService(database, approvals=approvals, notifications=notifications)
    facade, model = ResearchData(), CandidateModel()
    workflow = OpportunityDiscoveryWorkflow(
        database,
        facade,
        opportunities,
        snapshots,
        StructuredModelRunner(model_factory=lambda: model),
    )
    request = OpportunityDiscoveryRequest(
        query="waterjet",
        changed_by_actor_id=actor["id"],
        default_approver_actor_id=actor["id"],
        idempotency_key="discovery-1",
    )
    yield database, snapshots, site, actor, scan, approvals, facade, model, workflow, request
    await snapshots.shutdown()
    await database.shutdown()


@pytest.mark.asyncio
async def test_discovery_freezes_evidence_uses_existing_policy_and_reuses_receipt(context):
    database, snapshots, site, actor, scan, approvals, facade, model, workflow, request = context
    result = await workflow.execute(scan["id"], request)
    assert result["opportunity_ids"] and len(result["evidence_snapshot_ids"]) == 3
    opportunity = await OpportunityService(database).get(result["opportunity_ids"][0])
    assert opportunity["status"] == "WAITING_FOR_DECISION"
    observation = await database.fetch_one("SELECT * FROM opportunity_observations")
    assert observation["search_volume"] == 100 and observation["keyword_difficulty"] == 30
    for evidence_id in result["evidence_snapshot_ids"]:
        evidence = await snapshots.get_snapshot(evidence_id)
        assert evidence["meta"]["business_site_id"] == site["id"]
    repeated = await workflow.execute(scan["id"], request)
    assert repeated["opportunity_ids"] == result["opportunity_ids"]
    assert model.calls == 1 and len(facade.calls) == 3
    assert await database.fetch_value("SELECT COUNT(*) FROM opportunity_observations") == 1
    assert await database.fetch_value("SELECT COUNT(*) FROM approval_actions") == 1
    assert await database.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0
    tasks = TaskDeliveryService(database)
    with pytest.raises(ValueError, match="审批"):
        await tasks.create_task(
            site_id=site["id"],
            title="Plan",
            task_type="ARTICLE",
            priority="HIGH",
            opportunity_ids=result["opportunity_ids"],
            owner_actor_id=actor["id"],
        )
    action = (await approvals.list_pending(site["id"]))[0]
    await approvals.decide(action_id=action["id"], actor_id=actor["id"], decision="APPROVE")
    task = await tasks.create_task(
        site_id=site["id"],
        title="Plan",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=result["opportunity_ids"],
        owner_actor_id=actor["id"],
    )
    assert task["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "foreign_evidence",
        "foreign_market",
        "invented_keyword",
        "invented_page",
        "wrong_support",
        "invalid_schema",
    ],
)
async def test_invalid_candidate_never_writes_opportunities_or_approvals(context, mode):
    database, _, _, _, scan, _, _, model, workflow, request = context
    model.mode = mode
    expected = InternalProviderResponseError if mode == "invalid_schema" else ValueError
    with pytest.raises(expected):
        await workflow.execute(scan["id"], request)
    assert await database.fetch_value("SELECT COUNT(*) FROM opportunities") == 0
    assert await database.fetch_value("SELECT COUNT(*) FROM approval_actions") == 0
    assert (await ScanRunService(database).get(scan["id"]))["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_discovery_serializes_duplicate_requests_and_rejects_changed_key(context):
    database, _, _, _, scan, _, facade, model, workflow, request = context
    first, second = await asyncio.gather(
        workflow.execute(scan["id"], request), workflow.execute(scan["id"], request)
    )
    assert first["opportunity_ids"] == second["opportunity_ids"]
    assert model.calls == 1 and len(facade.calls) == 3
    with pytest.raises(ValueError, match="幂等"):
        await workflow.execute(scan["id"], request.model_copy(update={"query": "other"}))
    assert await database.fetch_value("SELECT COUNT(*) FROM opportunities") == 1


@pytest.mark.asyncio
async def test_two_workflow_instances_cannot_duplicate_business_writes(context):
    database, snapshots, _, _, scan, _, facade, model, workflow, request = context
    second_workflow = OpportunityDiscoveryWorkflow(
        database, facade, workflow.opportunities, snapshots, workflow.model_runner
    )
    first, second = await asyncio.gather(
        workflow.execute(scan["id"], request), second_workflow.execute(scan["id"], request)
    )
    assert first["opportunity_ids"] == second["opportunity_ids"]
    assert model.calls <= 2
    for table in ("opportunities", "opportunity_observations", "approval_actions"):
        assert await database.fetch_value(f"SELECT COUNT(*) FROM {table}") == 1


@pytest.mark.asyncio
async def test_completed_scan_rejects_new_request_and_model_respects_candidate_bound(context):
    database, _, _, _, scan, _, facade, model, workflow, request = context
    model.count = 2
    with pytest.raises(ValueError, match="数量"):
        await workflow.execute(scan["id"], request.model_copy(update={"max_opportunities": 1}))
    assert await database.fetch_value("SELECT COUNT(*) FROM opportunities") == 0
    model.count = 1
    await workflow.execute(scan["id"], request)
    calls = len(facade.calls)
    with pytest.raises(ValueError, match="不可执行"):
        await workflow.execute(
            scan["id"], request.model_copy(update={"idempotency_key": "another"})
        )
    assert len(facade.calls) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_database_failure_rolls_back_entire_batch_and_approval_queue(
    context, monkeypatch, error
):
    database, _, _, _, scan, _, _, model, workflow, request = context
    model.count = 2
    original = OpportunityService.upsert_candidate
    calls = 0

    async def fail_second(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error("database interruption")
        return await original(self, **kwargs)

    monkeypatch.setattr(OpportunityService, "upsert_candidate", fail_second)
    with pytest.raises(error):
        await workflow.execute(scan["id"], request)
    for table in (
        "opportunities",
        "opportunity_observations",
        "approval_actions",
        "notification_events",
        "work_records",
    ):
        assert await database.fetch_value(f"SELECT COUNT(*) FROM {table}") == 0
    assert (await ScanRunService(database).get(scan["id"]))["status"] == "RUNNING"
    monkeypatch.setattr(OpportunityService, "upsert_candidate", original)
    result = await workflow.execute(scan["id"], request)
    assert len(result["opportunity_ids"]) == 2
    assert await database.fetch_value("SELECT COUNT(*) FROM opportunity_observations") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("score", "status"), [(50, "OBSERVED"), (70, "OPPORTUNITY_POOL")])
async def test_low_and_medium_opportunities_keep_existing_approval_rules(context, score, status):
    database, _, _, _, scan, _, _, model, workflow, request = context
    model.score = score
    result = await workflow.execute(scan["id"], request)
    opportunity = await OpportunityService(database).get(result["opportunity_ids"][0])
    assert opportunity["status"] == status
    assert await database.fetch_value("SELECT COUNT(*) FROM approval_actions") == 0
    assert await database.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_high_opportunity_without_selected_approver_only_enters_pool(context):
    database, _, _, _, scan, _, _, _, workflow, request = context
    result = await workflow.execute(
        scan["id"], request.model_copy(update={"default_approver_actor_id": None})
    )
    opportunity = await OpportunityService(database).get(result["opportunity_ids"][0])
    assert opportunity["status"] == "WAITING_FOR_DECISION"
    assert await database.fetch_value("SELECT COUNT(*) FROM approval_actions") == 0
    assert await database.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_empty_generation_completes_without_manufacturing_opportunities(context):
    database, _, _, _, scan, _, _, model, workflow, request = context
    model.count = 0
    result = await workflow.execute(scan["id"], request)
    assert result["opportunity_ids"] == []
    assert (await ScanRunService(database).get(scan["id"]))["status"] == "COMPLETED"
    assert await database.fetch_value("SELECT COUNT(*) FROM approval_actions") == 0


@pytest.mark.asyncio
async def test_missing_model_or_data_configuration_does_not_spend_queries(context, monkeypatch):
    database, _, _, _, scan, _, facade, model, workflow, request = context

    def missing_model():
        raise ValueError("DEEPSEEK_API_KEY 未配置")

    monkeypatch.setattr(workflow.model_runner, "model_factory", missing_model)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        await workflow.execute(scan["id"], request)
    assert not facade.calls and model.calls == 0
    monkeypatch.setattr(facade, "live_capabilities", lambda: set())
    with pytest.raises(InternalDataNotAvailableError):
        await workflow.execute(scan["id"], request)
    assert not facade.calls
    assert await database.fetch_value("SELECT COUNT(*) FROM opportunities") == 0


@pytest.mark.asyncio
async def test_partial_research_keeps_unknown_metrics_without_zero_filling(context, monkeypatch):
    database, _, _, _, scan, _, facade, _, workflow, request = context
    monkeypatch.setattr(
        facade, "live_capabilities", lambda: {Capability.AUTOCOMPLETE, Capability.SERP_OVERVIEW}
    )
    result = await workflow.execute(scan["id"], request)
    assert result["workflow_status"] == "partial"
    assert len(result["evidence_snapshot_ids"]) == 2
    observation = await database.fetch_one("SELECT * FROM opportunity_observations")
    assert observation["search_volume"] is None and observation["keyword_difficulty"] is None


@pytest.mark.asyncio
async def test_search_redirect_is_kept_as_raw_evidence_but_not_a_competitor(context, monkeypatch):
    database, snapshots, _, _, scan, _, facade, model, workflow, request = context
    query = facade.query_live

    async def redirected(**kwargs):
        response = await query(**kwargs)
        if kwargs['capability'] == Capability.SERP_OVERVIEW:
            response['data'][0]['url'] = 'https://google.com/goto?url=opaque'
        return response

    monkeypatch.setattr(facade, 'query_live', redirected)
    model.mode = 'search_redirect'
    result = await workflow.execute(scan['id'], request)
    assert result['opportunity_ids']
    observation = await database.fetch_one('SELECT * FROM opportunity_observations')
    assert observation['competitor_count'] is None
    assert '未确认真实竞品' in observation['gap_summary']
    assert await database.fetch_value('SELECT COUNT(*) FROM competitor_pages') == 0
    frozen = [await snapshots.get_snapshot(key) for key in result['evidence_snapshot_ids']]
    assert 'https://google.com/goto?url=opaque' in str(frozen)


@pytest.mark.asyncio
async def test_inactive_scope_and_unknown_actor_fail_before_external_queries(context):
    database, _, site, _, scan, _, facade, model, workflow, request = context
    with pytest.raises(KeyError):
        await workflow.execute(
            scan["id"], request.model_copy(update={"changed_by_actor_id": "unknown"})
        )
    await database.execute("UPDATE sites SET status='INACTIVE' WHERE id=?", (site["id"],))
    with pytest.raises(ValueError):
        await workflow.execute(scan["id"], request)
    assert not facade.calls and model.calls == 0


@pytest.mark.asyncio
async def test_discovery_keeps_research_model_and_writes_in_one_trace(context, tmp_path):
    _, _, _, _, scan, _, _, model, workflow, request = context
    observations = ObservabilityDatabase(tmp_path / "observations.db")
    observer = ObservabilityClient(observations, project_key="seo-agent")
    workflow.observability = observer
    workflow.model_runner.observability = observer
    try:
        await observer.startup()
        registry = PromptRegistry(observations)
        seeded = await SeoPromptSeeder(registry).seed()
        workflow.model_runner.prompt_registry = registry
        result = await workflow.execute(scan["id"], request)
        spans = await observations.fetch_all("SELECT * FROM obs_spans")
        by_name = {row["operation_name"]: row for row in spans}
        root = by_name["seo.opportunity_discovery"]
        for name in (
            "seo_keyword_opportunity",
            "seo.opportunity.discover",
            "freeze_opportunity_evidence",
            "validate_opportunity_candidates",
            "persist_opportunity_candidates",
        ):
            assert by_name[name]["trace_id"] == root["trace_id"]
            assert by_name[name]["parent_span_id"] == root["id"]
        calls = await observations.fetch_all("SELECT * FROM obs_model_calls")
        assert len(calls) == 1 and "evidence_snapshot_ids" in str(calls)
        assert calls[0]["prompt_version_id"] == seeded["seo.opportunity.discover"]["id"]
        assert "waterjet" in str(calls) and "SystemMessage" not in str(calls)
        assert result["trace_id"] == root["trace_id"]
        await workflow.execute(scan["id"], request)
        assert model.calls == 1
        assert await observations.fetch_one(
            "SELECT * FROM obs_spans WHERE operation_name='seo.opportunity_discovery.reused'"
        )
    finally:
        await observer.shutdown()


@pytest.mark.asyncio
async def test_discovery_api_scope_validation_and_success(context, monkeypatch):
    from src.api.routes import seo_agent_opportunities as route

    _, _, _, _, scan, _, facade, _, workflow, request = context
    monkeypatch.setattr(route, "opportunity_discovery_workflow", workflow)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/v1/seo-agent/scan-runs/{scan['id']}/discover-opportunities"
        invalid = await client.post(path, json={**request.model_dump(), "site_id": "other-site"})
        assert invalid.status_code == 422 and not facade.calls
        missing = await client.post(path.replace(scan["id"], "unknown"), json=request.model_dump())
        assert missing.status_code == 404 and not facade.calls
        result = await client.post(path, json=request.model_dump())
        assert result.status_code == 200 and result.json()["opportunity_ids"]


@pytest.mark.asyncio
async def test_discovery_api_reports_model_failure_without_partial_business_writes(
    context, monkeypatch
):
    from src.api.routes import seo_agent_opportunities as route

    database, _, _, _, scan, _, _, model, workflow, request = context
    model.mode = "upstream_failure"
    monkeypatch.setattr(route, "opportunity_discovery_workflow", workflow)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/seo-agent/scan-runs/{scan['id']}/discover-opportunities",
            json=request.model_dump(),
        )
    assert response.status_code == 503
    assert "private" not in response.text
    assert await database.fetch_value("SELECT COUNT(*) FROM opportunities") == 0
    assert await database.fetch_value("SELECT COUNT(*) FROM approval_actions") == 0
