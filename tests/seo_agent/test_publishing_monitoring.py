from __future__ import annotations

# ruff: noqa: E501
import asyncio
from pathlib import Path

import pytest
from aiohttp import web
from sqlalchemy import func, insert, select, update

from src.observability.client import ObservabilityClient
from src.observability.schema import ToolCallRow
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    DeliveryRow,
    MonitoringCheckRow,
    MonitoringOutcomeGoalRow,
    OpportunityRow,
    PublishRecordRow,
)
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
async def published_site(tmp_path: Path):
    directory = tmp_path / "site"
    directory.mkdir()
    app = web.Application()
    app.router.add_static("/", directory)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        port = runner.addresses[0][1]
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.fixture
async def context(tmp_path: Path, approve_opportunity, pg_url: str):
    db = SeoAgentDatabase(pg_url)
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
        insert(OpportunityRow).values(
            id="opp-1",
            site_id=site["id"],
            fingerprint="fp",
            normalized_intent="intent",
            opportunity_type="CONTENT_GAP",
            target_country="US",
            target_language="en",
            title="Opportunity",
            current_score=85,
            priority_level="HIGH",
            status="WAITING_FOR_DECISION",
            policy_version="v1",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
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
async def test_html_site_publish_is_persisted_as_observed_tool_call(
    tmp_path: Path, pg_url: str
) -> None:
    database = ObservabilityDatabase(pg_url)
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
        call = await database.fetch_one(select(ToolCallRow.__table__))
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
            update(DeliveryRow)
            .where(DeliveryRow.id == delivery["id"])
            .values(
                structured_content_json=stored_content,
            )
        )

    with pytest.raises(ValueError, match="验收"):
        await service.publish_delivery(
            task_id=task["id"],
            delivery_id=delivery["id"],
            slug="guide",
            html_content=html_content,
            submitted_by_actor_id=owner["id"],
            idempotency_key="publish:unreviewed",
            target_environment="preview",
        )
    assert not (tmp_path / "site/guide.html").exists()
    assert await db.fetch_value(select(func.count()).select_from(PublishRecordRow)) == 0


@pytest.mark.asyncio
async def test_publish_confirmation_creates_default_indexing_checks(
    context, published_site
) -> None:
    db, _, owner, task, delivery, tmp_path = context
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(
            root_path=tmp_path / "site", base_url=published_site, content_subdir="pages"
        )
    )
    monitoring = MonitoringService(db)
    service = PublishingService(db, adapter, monitoring)

    async def publish():
        return await service.publish_delivery(
            task_id=task["id"],
            delivery_id=delivery["id"],
            slug="guide",
            html_content="<h1>Guide</h1>",
            submitted_by_actor_id=owner["id"],
            idempotency_key="publish:guide",
            target_environment="preview",
        )

    record, repeated = await asyncio.gather(publish(), publish())
    assert repeated["id"] == record["id"]

    async def confirm():
        return await service.confirm_publish(
            record["id"], confirmed_by_actor_id=owner["id"], published_url=record["candidate_url"]
        )

    confirmed, replay = await asyncio.gather(confirm(), confirm())
    assert confirmed["confirmed_at"] == replay["confirmed_at"]
    plan = await monitoring.get_plan_by_publish(record["id"])
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["error_code"] is None
    assert plan is not None
    checks = await db.fetch_all(
        select(MonitoringCheckRow.__table__)
        .where(MonitoringCheckRow.monitoring_plan_id == plan["id"])
        .order_by(MonitoringCheckRow.sequence_no)
    )
    assert [item["sequence_no"] for item in checks] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_indexing_then_ranking_goal_evaluation_is_deterministic(
    context, published_site
) -> None:
    db, _, owner, task, delivery, tmp_path = context
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(root_path=tmp_path / "site", base_url=published_site)
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
            select(MonitoringCheckRow.__table__)
            .where(MonitoringCheckRow.monitoring_plan_id == plan["id"])
            .order_by(MonitoringCheckRow.sequence_no)
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
        select(MonitoringOutcomeGoalRow.__table__).where(
            MonitoringOutcomeGoalRow.monitoring_outcome_id == outcome["id"],
            MonitoringOutcomeGoalRow.monitoring_goal_id == goal["id"],
        )
    )
    assert outcome["outcome"] == "EFFECTIVE"
    assert link["status"] == "ACHIEVED"
