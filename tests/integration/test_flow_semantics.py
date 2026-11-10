from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.promptops.registry import PromptRegistry
from src.seo_agent.approvals.policy import ActionPolicy
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.automation.models import DeliveryReviewOutput
from src.seo_agent.automation.prompts import SeoPromptSeeder
from src.seo_agent.automation.structured import StructuredModelAutomation
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.diagnosis.contracts import DiagnosisCoordinator, DiagnosisRequest
from src.seo_agent.monitoring.execution import (
    IndexingObservation,
    MonitoringExecutionService,
    RankingObservation,
)
from src.seo_agent.monitoring.service import MonitoringService
from src.seo_agent.opportunities.models import OpportunityCandidate, OpportunityScoreFactors
from src.seo_agent.opportunities.normalizer import OpportunityCandidateNormalizer
from src.seo_agent.publishing.html_site import (
    HtmlPublishResult,
    PublishVerification,
)
from src.seo_agent.publishing.service import PublishingService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService


class FakeRunnable:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def ainvoke(self, _: Any) -> Any:
        return self.value


class FakeStructuredModel:
    provider_name = "fake"
    model_name = "fake-structured"

    def __init__(self, responses: dict[type, Any]) -> None:
        self.responses = responses

    def with_structured_output(self, schema: type, **_: Any) -> FakeRunnable:
        return FakeRunnable(self.responses[schema])


class FakeDiagnosisEngine:
    async def diagnose(self, request: DiagnosisRequest) -> list[OpportunityCandidate]:
        assert request.evidence_snapshot_ids == ["snapshot-1"]
        return [candidate("  WaterJet   Maintenance COST  ")]


class FakePublishAdapter:
    async def publish(self, request: Any) -> HtmlPublishResult:
        return HtmlPublishResult(
            success=True,
            file_path=f"/tmp/{request.slug}.html",
            published_url="https://example.com/page.html",
            checksum="checksum",
        )

    async def verify(self, published_url: str) -> PublishVerification:
        return PublishVerification(reachable=True, status_code=200)


class FakeIndexingCollector:
    async def collect(self, *, published_url: str) -> IndexingObservation:
        assert published_url == "https://example.com/page.html"
        return IndexingObservation(indexed=True, evidence_snapshot_id="snapshot-index")


def candidate(intent: str = "waterjet maintenance cost") -> OpportunityCandidate:
    return OpportunityCandidate(
        normalized_intent=intent,
        opportunity_type=" content_gap ",
        target_country="us",
        target_language="EN",
        title="  Waterjet maintenance cost guide  ",
        summary="  Competitors cover cost breakdown.  ",
        keywords=[" WaterJet  Maintenance Cost ", "waterjet maintenance cost"],
        competitor_pages=[
            "HTTPS://COMPETITOR.COM/Guide/#section",
            "https://competitor.com/Guide/",
        ],
        evidence_snapshot_ids=["snapshot-1"],
        score_factors=OpportunityScoreFactors(
            search_demand=80,
            business_intent=90,
            content_gap=85,
            difficulty_fit=75,
            site_fit=80,
            evidence_confidence=90,
        ),
    )


def prd_content() -> TaskPrdContent:
    return TaskPrdContent(
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


@pytest.mark.asyncio
async def test_diagnosis_coordinator_uses_explicit_engine_and_normalizes_candidates() -> None:
    coordinator = DiagnosisCoordinator(
        engine=FakeDiagnosisEngine(),
        normalizer=OpportunityCandidateNormalizer(),
    )
    result = await coordinator.diagnose(
        DiagnosisRequest(
            site_id="site-1",
            scan_run_id="scan-1",
            evidence_snapshot_ids=["snapshot-1"],
        )
    )
    assert len(result) == 1
    assert result[0].normalized_intent == "waterjet maintenance cost"
    assert result[0].opportunity_type == "CONTENT_GAP"
    assert result[0].target_country == "US"
    assert result[0].target_language == "en"
    assert result[0].keywords == ["waterjet maintenance cost"]
    assert result[0].competitor_pages == ["https://competitor.com/Guide"]


def test_candidate_normalizer_produces_stable_fingerprint() -> None:
    normalizer = OpportunityCandidateNormalizer()
    first = normalizer.normalize(candidate("Waterjet maintenance COST"))
    second = normalizer.normalize(candidate(" waterjet   maintenance cost "))
    assert normalizer.fingerprint("site-1", first) == normalizer.fingerprint("site-1", second)


def test_action_policy_is_specific_versioned_and_fail_closed() -> None:
    policy = ActionPolicy()
    opportunity = policy.evaluate(
        action_type="ADD_OPPORTUNITY_TO_PRODUCTION",
        target_environment="production",
    )
    preview = policy.evaluate(
        action_type="PUBLISH_CONTENT",
        target_environment="preview",
    )
    unknown = policy.evaluate(
        action_type="UNKNOWN_EXTERNAL_WRITE",
        target_environment="production",
    )
    assert opportunity.requires_approval is True
    assert opportunity.risk_level == "HIGH"
    assert preview.requires_approval is False
    assert unknown.requires_approval is True
    assert unknown.reason == "unknown_write_action_fail_closed"
    assert opportunity.policy_version == policy.version


@pytest.mark.asyncio
async def test_prd_and_review_business_outputs_store_prompt_version(tmp_path: Path) -> None:
    business = SeoAgentDatabase(tmp_path / "seo-agent.db")
    observability = ObservabilityDatabase(tmp_path / "observability.db")
    await business.startup()
    await observability.startup()
    registry = PromptRegistry(observability)
    await registry.startup()
    seeded = await SeoPromptSeeder(registry).seed()
    system = SeoAgentSystemService(business)
    site = await system.create_site(
        name="Demo",
        domain="demo.test",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@test.com")
    executor = await system.create_actor(
        actor_type="HUMAN_OPERATOR", name="Writer", email="writer@test.com"
    )
    now = "2026-07-24T00:00:00+00:00"
    await business.execute(
        "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, opportunity_type, "
        "target_country, target_language, title, current_score, priority_level, status, "
        "current_summary, policy_version, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES ('opp-1', ?, 'fp', 'maintenance cost', 'CONTENT_GAP', 'US', 'en', "
        "'Maintenance Cost', 88, 'HIGH', 'APPROVED', 'pricing gap', 'v1', ?, ?, ?, ?)",
        (site["id"], now, now, now, now),
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
    review_output = DeliveryReviewOutput(
        status="ACCEPTED",
        score=92,
        passed_items=[],
        failed_items=[],
        missing_items=[],
        revision_instructions=[],
    )
    model = FakeStructuredModel(
        {TaskPrdContent: prd_content(), DeliveryReviewOutput: review_output}
    )
    automation = StructuredModelAutomation(
        business,
        tasks,
        model_factory=lambda: model,
        prompt_registry=registry,
    )
    try:
        prd = await automation.generate_prd(
            task_id=task["id"],
            created_by_actor_id=owner["id"],
        )
        assert prd["prompt_version_id"] == seeded["seo.prd.generate"]["id"]
        delivery = await tasks.submit_delivery(
            task_id=task["id"],
            prd_version_id=prd["id"],
            title="Draft",
            summary="ready",
            structured_content={"body": "cost factors"},
            submitted_by_actor_id=executor["id"],
        )
        review = await automation.review_delivery(
            delivery_id=delivery["id"],
            reviewer_actor_id=owner["id"],
        )
        assert review["prompt_version_id"] == seeded["seo.delivery.review"]["id"]
        assert review["prd_version_id"] == prd["id"]
    finally:
        await business.shutdown()
        await observability.shutdown()


@pytest.mark.asyncio
async def test_trace_links_are_queryable_from_both_sides(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    client = ObservabilityClient(database, project_key="seo-agent")
    try:
        async with client.trace("seo.publish") as publish_trace:
            pass
        async with client.trace("seo.monitoring.indexing") as monitoring_trace:
            link = await client.link_traces(
                source_trace_id=publish_trace.trace_id,
                target_trace_id=monitoring_trace.trace_id,
                relation_type="FOLLOW_UP",
                attributes={"monitoring_plan_id": "plan-1"},
            )
        publish = await client.get_trace(publish_trace.trace_id)
        monitoring = await client.get_trace(monitoring_trace.trace_id)
        assert link["relation_type"] == "FOLLOW_UP"
        assert publish["outgoing_links"][0]["target_trace_id"] == monitoring_trace.trace_id
        assert monitoring["incoming_links"][0]["source_trace_id"] == publish_trace.trace_id
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_monitoring_check_runs_in_new_linked_trace(tmp_path: Path) -> None:
    business = SeoAgentDatabase(tmp_path / "seo-agent.db")
    obs_db = ObservabilityDatabase(tmp_path / "observability.db")
    await business.startup()
    await obs_db.startup()
    observer = ObservabilityClient(obs_db, project_key="seo-agent")
    system = SeoAgentSystemService(business)
    site = await system.create_site(
        name="Demo",
        domain="example.com",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@test.com")
    now = "2026-07-24T00:00:00+00:00"
    await business.execute(
        "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, opportunity_type, "
        "target_country, target_language, title, current_score, priority_level, status, "
        "policy_version, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES ('opp-1', ?, 'fp', 'intent', 'CONTENT_GAP', 'US', 'en', 'Opportunity', "
        "85, 'HIGH', 'APPROVED', 'v1', ?, ?, ?, ?)",
        (site["id"], now, now, now, now),
    )
    tasks = TaskDeliveryService(business, artifact_root=tmp_path / "artifacts")
    task = await tasks.create_task(
        site_id=site["id"],
        title="Guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=["opp-1"],
        owner_actor_id=owner["id"],
    )
    prd = await tasks.create_prd_version(
        task_id=task["id"],
        title="PRD",
        content=prd_content(),
        created_by_actor_id=owner["id"],
    )
    delivery = await tasks.submit_delivery(
        task_id=task["id"],
        prd_version_id=prd["id"],
        title="Page",
        summary="Ready",
        structured_content={"html": "<h1>Guide</h1>"},
        submitted_by_actor_id=owner["id"],
    )
    await tasks.review_delivery(
        delivery_id=delivery["id"],
        prd_version_id=prd["id"],
        reviewer_actor_id=owner["id"],
        status="ACCEPTED",
        score=95,
    )
    try:
        monitoring = MonitoringService(business)
        publishing = PublishingService(
            business,
            FakePublishAdapter(),
            monitoring,
            observability=observer,
        )
        with pytest.raises(ValueError, match="生产发布需要已批准"):
            await publishing.publish_delivery(
                task_id=task["id"],
                delivery_id=delivery["id"],
                slug="blocked-page",
                html_content="<h1>Guide</h1>",
                submitted_by_actor_id=owner["id"],
                idempotency_key="publish:blocked",
            )

        approvals = ApprovalService(business)
        approval = await approvals.create_action(
            site_id=site["id"],
            action_type="PUBLISH_CONTENT",
            risk_level="HIGH",
            target_type="delivery",
            target_id=delivery["id"],
            requested_payload={"target_environment": "production"},
            approver_actor_id=owner["id"],
            idempotency_key="approval:publish-1",
            requested_by_actor_id=owner["id"],
        )
        await approvals.decide(
            action_id=approval["id"],
            actor_id=owner["id"],
            decision="APPROVE",
        )
        record = await publishing.publish_delivery(
            task_id=task["id"],
            delivery_id=delivery["id"],
            slug="page",
            html_content="<h1>Guide</h1>",
            submitted_by_actor_id=owner["id"],
            idempotency_key="publish:1",
            approval_action_id=approval["id"],
        )
        assert record["source_trace_id"]
        assert record["approval_action_id"] == approval["id"]
        await publishing.confirm_publish(
            record["id"],
            confirmed_by_actor_id=owner["id"],
            published_url="https://example.com/page.html",
        )
        origin_trace_id = record["source_trace_id"]
        plan = await monitoring.get_plan_by_publish(record["id"])
        assert plan is not None
        assert plan["origin_trace_id"] == origin_trace_id
        check = await business.fetch_one(
            "SELECT * FROM monitoring_checks WHERE monitoring_plan_id = ? ORDER BY sequence_no",
            (plan["id"],),
        )
        assert check is not None
        execution = MonitoringExecutionService(business, monitoring, observer)
        result = await execution.run_indexing_check(
            check_id=check["id"],
            collector=FakeIndexingCollector(),
        )
        stored_check = await business.fetch_one(
            "SELECT * FROM monitoring_checks WHERE id = ?", (check["id"],)
        )
        assert stored_check is not None
        assert stored_check["trace_id"] == result.trace_id
        link = await obs_db.fetch_one(
            "SELECT * FROM obs_trace_links WHERE source_trace_id = ? AND target_trace_id = ?",
            (origin_trace_id, result.trace_id),
        )
        assert link is not None
        assert link["relation_type"] == "FOLLOW_UP"

        ranking_check = await monitoring.create_ranking_check(
            plan["id"],
            sequence_no=1,
        )
        ranking_result = await execution.ingest_ranking_result(
            check_id=ranking_check["id"],
            keyword="maintenance cost",
            observation=RankingObservation(
                position=12,
                evidence_snapshot_id="snapshot-ranking",
            ),
        )
        stored_ranking = await business.fetch_one(
            "SELECT * FROM monitoring_checks WHERE id = ?",
            (ranking_check["id"],),
        )
        assert stored_ranking is not None
        assert stored_ranking["trace_id"] == ranking_result["execution_trace_id"]
        ranking_link = await obs_db.fetch_one(
            "SELECT * FROM obs_trace_links WHERE source_trace_id = ? AND target_trace_id = ?",
            (origin_trace_id, ranking_result["execution_trace_id"]),
        )
        assert ranking_link is not None
    finally:
        await business.shutdown()
        await obs_db.shutdown()
