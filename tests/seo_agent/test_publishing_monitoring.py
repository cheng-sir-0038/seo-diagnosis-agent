from __future__ import annotations

# ruff: noqa: E501
from pathlib import Path

import pytest

from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.monitoring.service import MonitoringService
from src.seo_agent.publishing.html_site import (
    HtmlPublishRequest,
    HtmlSitePublishAdapter,
    HtmlSiteSettings,
)
from src.seo_agent.publishing.service import PublishingService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService


@pytest.fixture
async def context(tmp_path: Path, approve_opportunity):
    db = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Demo",
        domain="publish.test",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@qq.com")
    executor = await system.create_actor(
        actor_type="HUMAN_OPERATOR", name="Writer", email="writer@qq.com"
    )
    now = "2026-07-23T00:00:00+00:00"
    await db.execute(
        "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, opportunity_type, target_country, target_language, title, current_score, priority_level, status, policy_version, first_seen_at, last_seen_at, created_at, updated_at) VALUES ('opp-1', ?, 'fp', 'intent', 'CONTENT_GAP', 'US', 'en', 'Opportunity', 85, 'HIGH', 'WAITING_FOR_DECISION', 'v1', ?, ?, ?, ?)",
        (site["id"], now, now, now, now),
    )
    await approve_opportunity(db, site["id"], "opp-1", owner["id"])
    tasks = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    task = await tasks.create_task(
        site_id=site["id"],
        title="Guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=["opp-1"],
        owner_actor_id=owner["id"],
    )
    prd_content = TaskPrdContent(
        background="b",
        target_intent="intent",
        target_keywords=["keyword"],
        target_audience="audience",
        competitor_gap="gap",
        content_type="article",
        content_outline=["outline"],
        seo_title="Title",
        seo_description="Description",
        acceptance_criteria=["complete"],
        monitoring_goals=[{"type": "TOP20", "keyword": "keyword", "target": 20}],
    )
    prd = await tasks.create_prd_version(
        task_id=task["id"], title="PRD", content=prd_content, created_by_actor_id=owner["id"]
    )
    delivery = await tasks.submit_delivery(
        task_id=task["id"],
        prd_version_id=prd["id"],
        title="Page",
        summary="Ready",
        structured_content={"html": "<h1>Guide</h1>"},
        submitted_by_actor_id=executor["id"],
    )
    await tasks.review_delivery(
        delivery_id=delivery["id"],
        prd_version_id=prd["id"],
        reviewer_actor_id=owner["id"],
        status="ACCEPTED",
        score=95,
    )
    yield db, site, owner, task, delivery, tmp_path
    await db.shutdown()


@pytest.mark.asyncio
async def test_html_site_publisher_writes_atomic_file_and_rejects_traversal(tmp_path: Path) -> None:
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(
            root_path=tmp_path / "site", base_url="https://example.com", content_subdir="pages"
        )
    )
    result = await adapter.publish(
        HtmlPublishRequest(
            slug="maintenance-guide", html_content="<h1>Guide</h1>", idempotency_key="publish:1"
        )
    )
    assert result.success is True
    assert (tmp_path / "site/pages/maintenance-guide.html").read_text() == "<h1>Guide</h1>"
    assert result.published_url == "https://example.com/pages/maintenance-guide.html"
    with pytest.raises(ValueError, match="slug"):
        await adapter.publish(
            HtmlPublishRequest(slug="../escape", html_content="x", idempotency_key="publish:2")
        )


@pytest.mark.asyncio
async def test_html_site_publish_is_persisted_as_observed_tool_call(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "publish-observability.db")
    await database.startup()
    client = ObservabilityClient(database, project_key="seo-agent")
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(
            root_path=tmp_path / "site",
            base_url="https://example.com",
            content_subdir="pages",
        ),
        observability=client,
    )
    try:
        result = await adapter.publish(
            HtmlPublishRequest(
                slug="observed-guide",
                html_content="<h1>Observed</h1>",
                idempotency_key="publish:observed",
            )
        )
        call = await database.fetch_one("SELECT * FROM obs_tool_calls")
        assert result.success is True
        assert call is not None
        assert call["tool_name"] == "html_site.publish"
        assert call["status"] == "COMPLETED"
    finally:
        await database.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_content", "html_content"),
    [(None, "<h1>Unreviewed</h1>"), ("{}", "<h1>Guide</h1>"), ("invalid", "<h1>Guide</h1>")],
)
async def test_publish_rejects_html_that_was_not_in_the_accepted_delivery(
    context, stored_content: str | None, html_content: str
) -> None:
    db, _, owner, task, delivery, tmp_path = context
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(root_path=tmp_path / "site", base_url="https://example.com")
    )
    service = PublishingService(db, adapter, MonitoringService(db))
    if stored_content is not None:
        await db.execute(
            "UPDATE deliveries SET structured_content_json = ? WHERE id = ?",
            (stored_content, delivery["id"]),
        )

    with pytest.raises(ValueError, match="验收"):
        await service.publish_delivery(
            task_id=task["id"], delivery_id=delivery["id"], slug="guide",
            html_content=html_content, submitted_by_actor_id=owner["id"],
            idempotency_key="publish:unreviewed", target_environment="preview",
        )
    assert not (tmp_path / "site/guide.html").exists()
    assert await db.fetch_value("SELECT COUNT(*) FROM publish_records") == 0


@pytest.mark.asyncio
async def test_publish_confirmation_creates_default_indexing_checks(context) -> None:
    db, _, owner, task, delivery, tmp_path = context
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(
            root_path=tmp_path / "site", base_url="https://example.com", content_subdir="pages"
        )
    )
    monitoring = MonitoringService(db)
    service = PublishingService(db, adapter, monitoring)
    record = await service.publish_delivery(
        task_id=task["id"],
        delivery_id=delivery["id"],
        slug="guide",
        html_content="<h1>Guide</h1>",
        submitted_by_actor_id=owner["id"],
        idempotency_key="publish:guide",
        target_environment="preview",
    )
    repeated = await service.publish_delivery(
        task_id=task["id"],
        delivery_id=delivery["id"],
        slug="guide",
        html_content="<h1>Guide</h1>",
        submitted_by_actor_id=owner["id"],
        idempotency_key="publish:guide",
        target_environment="preview",
    )
    assert repeated["id"] == record["id"]
    confirmed = await service.confirm_publish(
        record["id"], confirmed_by_actor_id=owner["id"], published_url=record["candidate_url"]
    )
    plan = await monitoring.get_plan_by_publish(record["id"])
    assert confirmed["status"] == "CONFIRMED"
    assert plan is not None
    checks = await db.fetch_all(
        "SELECT * FROM monitoring_checks WHERE monitoring_plan_id = ? ORDER BY sequence_no",
        (plan["id"],),
    )
    assert [item["sequence_no"] for item in checks] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_indexing_then_ranking_goal_evaluation_is_deterministic(context) -> None:
    db, _, owner, task, delivery, tmp_path = context
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(root_path=tmp_path / "site", base_url="https://example.com")
    )
    monitoring = MonitoringService(db)
    publishing = PublishingService(db, adapter, monitoring)
    record = await publishing.publish_delivery(
        task_id=task["id"],
        delivery_id=delivery["id"],
        slug="guide",
        html_content="<h1>Guide</h1>",
        submitted_by_actor_id=owner["id"],
        idempotency_key="publish:goal",
        target_environment="preview",
    )
    await publishing.confirm_publish(
        record["id"], confirmed_by_actor_id=owner["id"], published_url=record["candidate_url"]
    )
    plan = await monitoring.get_plan_by_publish(record["id"])
    assert plan is not None
    goal = await monitoring.add_goal(
        plan["id"],
        goal_type="TOP20",
        target_keyword="keyword",
        target_value=20,
        comparison_operator="<=",
    )
    check = (
        await db.fetch_all(
            "SELECT * FROM monitoring_checks WHERE monitoring_plan_id = ? ORDER BY sequence_no",
            (plan["id"],),
        )
    )[0]
    await monitoring.record_indexing_result(
        check["id"], indexed=True, evidence_snapshot_id="snapshot-1"
    )
    ranking_check = await monitoring.create_ranking_check(plan["id"], sequence_no=1)
    await monitoring.record_ranking_observation(
        ranking_check["id"], keyword="keyword", position=12, evidence_snapshot_id="snapshot-2"
    )
    outcome = await monitoring.evaluate_plan(plan["id"])
    link = await db.fetch_one(
        "SELECT * FROM monitoring_outcome_goals WHERE monitoring_outcome_id = ? AND monitoring_goal_id = ?",
        (outcome["id"], goal["id"]),
    )
    assert outcome["outcome"] == "EFFECTIVE"
    assert link["status"] == "ACHIEVED"
