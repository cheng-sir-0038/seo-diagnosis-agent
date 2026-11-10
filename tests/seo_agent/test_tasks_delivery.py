from __future__ import annotations

# ruff: noqa: E501
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.artifacts.renderers import MarkdownPrdRenderer, PdfPrdRenderer
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService


@pytest.fixture
async def context(tmp_path: Path):
    db = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Demo",
        domain="tasks.test",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@qq.com")
    executor = await system.create_actor(
        actor_type="HUMAN_OPERATOR", name="Writer", email="writer@qq.com"
    )
    now = "2026-07-23T00:00:00+00:00"
    opportunity_ids = []
    for index in range(2):
        oid = f"opp-{index}"
        await db.execute(
            "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, opportunity_type, target_country, target_language, title, current_score, priority_level, status, policy_version, first_seen_at, last_seen_at, created_at, updated_at) VALUES (?, ?, ?, ?, 'CONTENT_GAP', 'US', 'en', ?, 85, 'HIGH', 'WAITING_FOR_DECISION', 'v1', ?, ?, ?, ?)",
            (
                oid,
                site["id"],
                f"fp-{index}",
                f"intent-{index}",
                f"Opportunity {index}",
                now,
                now,
                now,
                now,
            ),
        )
        approvals = ApprovalService(db)
        action = await approvals.create_action(
            site_id=site["id"], action_type="ADD_OPPORTUNITY_TO_PRODUCTION", risk_level="HIGH",
            target_type="opportunity", target_id=oid, requested_payload={},
            approver_actor_id=owner["id"], idempotency_key=f"approval:{oid}",
        )
        await approvals.decide(action_id=action["id"], actor_id=owner["id"], decision="APPROVE")
        opportunity_ids.append(oid)
    yield db, site, owner, executor, opportunity_ids, tmp_path
    await db.shutdown()


def sample_prd() -> TaskPrdContent:
    return TaskPrdContent(
        background="竞品覆盖该意图，本站缺失",
        target_intent="waterjet maintenance cost",
        target_keywords=["waterjet maintenance cost", "waterjet service price"],
        target_audience="采购与维护负责人",
        competitor_gap="缺少成本拆解和 FAQ",
        content_type="article",
        content_outline=["成本构成", "维护周期", "FAQ"],
        required_questions=["维护成本是多少？"],
        seo_title="Waterjet Maintenance Cost Guide",
        seo_description="A practical cost guide.",
        internal_links=["/services"],
        prohibited_items=["虚构报价"],
        delivery_requirements=["Markdown", "事实来源"],
        acceptance_criteria=["覆盖所有问题", "包含内部链接"],
        monitoring_goals=[{"type": "TOP20", "keyword": "waterjet maintenance cost"}],
    )


@pytest.mark.asyncio
async def test_prd_api_preserves_validated_content(context, monkeypatch) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_tasks

    db, site, owner, _, opportunity_ids, tmp_path = context
    service = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    task = await service.create_task(
        site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
        opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
    )
    monkeypatch.setattr(seo_agent_tasks, "task_delivery_service", service)
    app = FastAPI()
    app.include_router(seo_agent_tasks.router)
    payload = {
        "title": "PRD", "content": sample_prd().model_dump(),
        "created_by_actor_id": owner["id"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(f"/seo-agent/tasks/{task['id']}/prd-versions", json=payload)
        assert first.status_code == 201
        assert first.json()["version_no"] == 1
        second = await client.post(
            f"/seo-agent/tasks/{task['id']}/prd-versions",
            json={**payload, "source_version_id": first.json()["id"]},
        )
        assert second.status_code == 201
        assert second.json()["source_version_id"] == first.json()["id"]
        invalid = await client.post(
            f"/seo-agent/tasks/{task['id']}/prd-versions",
            json={**payload, "content": {"background": "incomplete"}},
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["failed_items", "missing_items"])
async def test_review_with_unmet_criteria_cannot_accept_delivery(context, problem: str) -> None:
    db, site, owner, executor, opportunity_ids, tmp_path = context
    service = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    task = await service.create_task(
        site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
        opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
    )
    prd = await service.create_prd_version(
        task_id=task["id"], title="PRD", content=sample_prd(), created_by_actor_id=owner["id"]
    )
    delivery = await service.submit_delivery(
        task_id=task["id"], prd_version_id=prd["id"], title="Draft", summary="ready",
        structured_content={"html": "<h1>Draft</h1>"}, submitted_by_actor_id=executor["id"],
    )
    review = await service.review_delivery(
        delivery_id=delivery["id"], prd_version_id=prd["id"], reviewer_actor_id=owner["id"],
        status="ACCEPTED", score=95, **{problem: [{"item": "缺少必要证据"}]},
    )
    assert review["status"] == "REVISION_REQUIRED"
    assert await db.fetch_value("SELECT status FROM deliveries WHERE id = ?", (delivery["id"],)) == "REVISION_REQUIRED"
    assert (await service.get_task(task["id"]))["status"] == "REVISION_REQUIRED"


@pytest.mark.asyncio
async def test_task_rejects_pending_opportunity_without_creating_records(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    opportunity_id = opportunity_ids[0]
    approvals = ApprovalService(db)
    action = await db.fetch_one(
        "SELECT id FROM approval_actions WHERE target_id = ?", (opportunity_id,)
    )
    assert action is not None
    await approvals.decide(
        action_id=action["id"], actor_id=owner["id"], decision="DECISION_CORRECTED"
    )
    with pytest.raises(ValueError, match="审批"):
        await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=[opportunity_ids[1], opportunity_id], owner_actor_id=owner["id"],
        )
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM task_opportunities") == 0


@pytest.mark.asyncio
async def test_task_requires_latest_matching_approval(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    await ApprovalService(db).create_action(
        site_id=site["id"], action_type="ADD_OPPORTUNITY_TO_PRODUCTION", risk_level="HIGH",
        target_type="opportunity", target_id=opportunity_ids[0], requested_payload={},
        approver_actor_id=owner["id"], idempotency_key="approval:newer",
    )
    with pytest.raises(ValueError, match="审批"):
        await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
        )
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("priority", "status"), [("MEDIUM", "OPPORTUNITY_POOL"), ("LOW", "OBSERVED")]
)
async def test_task_rejects_unapproved_medium_and_low_opportunities(
    context, priority: str, status: str
) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    await db.execute(
        "UPDATE opportunities SET priority_level = ?, status = ? WHERE id = ?",
        (priority, status, opportunity_ids[0]),
    )
    await db.execute(
        "UPDATE approval_actions SET archived_at = '2026-01-01' WHERE target_id = ?",
        (opportunity_ids[0],),
    )
    with pytest.raises(ValueError, match="审批"):
        await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority=priority,
            opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
        )
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_task_accepts_edited_approval(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    action = await db.fetch_one(
        "SELECT id FROM approval_actions WHERE target_id = ?", (opportunity_ids[0],)
    )
    assert action is not None
    approvals = ApprovalService(db)
    await approvals.decide(
        action_id=action["id"], actor_id=owner["id"], decision="DECISION_CORRECTED"
    )
    await approvals.decide(
        action_id=action["id"], actor_id=owner["id"], decision="EDIT_AND_APPROVE",
        edited_payload={"recommended_task_types": ["FAQ"]},
    )
    task = await TaskDeliveryService(db).create_task(
        site_id=site["id"], title="FAQ", task_type="FAQ", priority="HIGH",
        opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
    )
    assert task["status"] == "DRAFT"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["other_action", "archived_action"])
async def test_task_rejects_approved_status_without_matching_action(context, change: str) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    if change == "other_action":
        await db.execute(
            "UPDATE approval_actions SET action_type = 'PUBLISH_CONTENT' WHERE target_id = ?",
            (opportunity_ids[0],),
        )
    else:
        await db.execute(
            "UPDATE approval_actions SET archived_at = '2026-01-01' WHERE target_id = ?",
            (opportunity_ids[0],),
        )
    with pytest.raises(ValueError, match="审批"):
        await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
        )
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_task_rolls_back_all_records_if_assignment_fails(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    await db.execute(
        "CREATE TRIGGER reject_task_assignment BEFORE INSERT ON task_assignments "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    with pytest.raises(aiosqlite.IntegrityError, match="forced failure"):
        await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=opportunity_ids, owner_actor_id=owner["id"],
        )
    for table in ("execution_tasks", "task_opportunities", "task_assignments", "task_status_history"):
        assert await db.fetch_value(f"SELECT COUNT(*) FROM {table}") == 0


@pytest.mark.asyncio
async def test_task_validation_waits_for_concurrent_approval_change(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    async with aiosqlite.connect(db.path) as connection:
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute(
            "UPDATE opportunities SET status = 'WAITING_FOR_DECISION' WHERE id = ?",
            (opportunity_ids[0],),
        )
        await connection.execute(
            "UPDATE approval_actions SET status = 'WAITING_FOR_APPROVAL' WHERE target_id = ?",
            (opportunity_ids[0],),
        )
        task = asyncio.create_task(TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
        ))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        await connection.commit()
        with pytest.raises(ValueError, match="审批"):
            await task
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_task_created_at_follows_concurrent_approval(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    action = await db.fetch_one(
        "SELECT id FROM approval_actions WHERE target_id = ?", (opportunity_ids[0],)
    )
    assert action is not None
    await ApprovalService(db).decide(
        action_id=action["id"], actor_id=owner["id"], decision="DECISION_CORRECTED"
    )
    async with aiosqlite.connect(db.path) as connection:
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute(
            "UPDATE opportunities SET status = 'APPROVED' WHERE id = ?", (opportunity_ids[0],)
        )
        await connection.execute(
            "UPDATE approval_actions SET status = 'APPROVED' WHERE id = ?", (action["id"],)
        )
        task = asyncio.create_task(TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
        ))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        approved_at = datetime.now(UTC)
        await connection.commit()
        created = await task
    assert datetime.fromisoformat(created["created_at"]) >= approved_at


@pytest.mark.asyncio
@pytest.mark.parametrize("use_duplicate", [False, True])
async def test_task_rejects_empty_or_duplicate_opportunities(context, use_duplicate: bool) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    ids = [opportunity_ids[0], opportunity_ids[0]] if use_duplicate else []
    with pytest.raises(ValueError):
        await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=ids, owner_actor_id=owner["id"],
        )
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_task_rejects_cross_site_and_unknown_opportunities_without_partial_records(context) -> None:
    db, _, owner, _, opportunity_ids, _ = context
    other = await SeoAgentSystemService(db).create_site(
        name="Other", domain="other.test", default_country="US", default_language="en", timezone="UTC"
    )
    service = TaskDeliveryService(db)
    for opportunity_id in (opportunity_ids[0], "missing"):
        with pytest.raises(KeyError):
            await service.create_task(
                site_id=other["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
                opportunity_ids=[opportunity_id], owner_actor_id=owner["id"],
            )
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM task_opportunities") == 0


@pytest.mark.asyncio
async def test_task_rejects_archived_opportunity(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    await db.execute(
        "UPDATE opportunities SET archived_at = '2026-01-01' WHERE id = ?", (opportunity_ids[0],)
    )
    with pytest.raises(KeyError):
        await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
            opportunity_ids=[opportunity_ids[0]], owner_actor_id=owner["id"],
        )
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_create_task_api_returns_actionable_errors(context, monkeypatch) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_tasks

    db, site, owner, _, opportunity_ids, _ = context
    approvals = ApprovalService(db)
    action = await db.fetch_one(
        "SELECT id FROM approval_actions WHERE target_id = ?", (opportunity_ids[0],)
    )
    assert action is not None
    await approvals.decide(
        action_id=action["id"], actor_id=owner["id"], decision="DECISION_CORRECTED"
    )
    monkeypatch.setattr(seo_agent_tasks, "task_delivery_service", TaskDeliveryService(db))
    app = FastAPI()
    app.include_router(seo_agent_tasks.router)
    payload = {
        "site_id": site["id"], "title": "Guide", "task_type": "ARTICLE", "priority": "HIGH",
        "opportunity_ids": [opportunity_ids[0]], "owner_actor_id": owner["id"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/seo-agent/tasks", json=payload)
        assert response.status_code == 400 and "审批" in response.json()["detail"]
        response = await client.post("/seo-agent/tasks", json={**payload, "opportunity_ids": []})
        assert response.status_code == 422
        response = await client.post("/seo-agent/tasks", json={**payload, "opportunity_ids": ["missing"]})
        assert response.status_code == 404
        await approvals.decide(action_id=action["id"], actor_id=owner["id"], decision="APPROVE")
        response = await client.post("/seo-agent/tasks", json=payload)
        assert response.status_code == 201
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 1


@pytest.mark.asyncio
async def test_task_links_multiple_opportunities_and_owner(context) -> None:
    db, site, owner, _, opportunity_ids, _ = context
    service = TaskDeliveryService(db)
    task = await service.create_task(
        site_id=site["id"],
        title="Maintenance guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=opportunity_ids,
        owner_actor_id=owner["id"],
    )
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM task_opportunities WHERE task_id = ?", (task["id"],)
        )
        == 2
    )
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM task_assignments WHERE task_id = ?", (task["id"],)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_prd_versions_render_stable_markdown_and_pdf(context) -> None:
    db, site, owner, _, opportunity_ids, tmp_path = context
    service = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    task = await service.create_task(
        site_id=site["id"],
        title="Guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=opportunity_ids,
        owner_actor_id=owner["id"],
    )
    v1 = await service.create_prd_version(
        task_id=task["id"], title="Guide PRD", content=sample_prd(), created_by_actor_id=owner["id"]
    )
    v2 = await service.create_prd_version(
        task_id=task["id"],
        title="Guide PRD revised",
        content=sample_prd(),
        created_by_actor_id=owner["id"],
        source_version_id=v1["id"],
        change_summary="Updated",
    )
    assert v1["version_no"] == 1 and v2["version_no"] == 2
    markdown = MarkdownPrdRenderer().render("Guide PRD", sample_prd())
    pdf = PdfPrdRenderer().render("Guide PRD", sample_prd())
    assert "waterjet maintenance cost" in markdown
    assert pdf.startswith(b"%PDF")
    artifacts = await service.render_prd_artifacts(v2["id"])
    assert {item["renderer_key"] for item in artifacts} == {"markdown", "pdf"}


@pytest.mark.asyncio
async def test_dispatch_delivery_and_review_are_versioned(context) -> None:
    db, site, owner, executor, opportunity_ids, tmp_path = context
    service = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    task = await service.create_task(
        site_id=site["id"],
        title="Guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=opportunity_ids,
        owner_actor_id=owner["id"],
    )
    prd = await service.create_prd_version(
        task_id=task["id"], title="PRD", content=sample_prd(), created_by_actor_id=owner["id"]
    )
    dispatch = await service.dispatch_task(
        task_id=task["id"],
        prd_version_id=prd["id"],
        recipient_actor_id=executor["id"],
        idempotency_key="dispatch:1",
    )
    repeated = await service.dispatch_task(
        task_id=task["id"],
        prd_version_id=prd["id"],
        recipient_actor_id=executor["id"],
        idempotency_key="dispatch:1",
    )
    assert repeated["id"] == dispatch["id"]
    delivery1 = await service.submit_delivery(
        task_id=task["id"],
        prd_version_id=prd["id"],
        title="Draft",
        summary="First",
        structured_content={"body": "draft"},
        submitted_by_actor_id=executor["id"],
    )
    review = await service.review_delivery(
        delivery_id=delivery1["id"],
        prd_version_id=prd["id"],
        reviewer_actor_id=owner["id"],
        status="REVISION_REQUIRED",
        score=70,
        failed_items=[{"key": "faq"}],
        revision_instructions=["补 FAQ"],
    )
    delivery2 = await service.submit_delivery(
        task_id=task["id"],
        prd_version_id=prd["id"],
        title="Draft v2",
        summary="Second",
        structured_content={"body": "revised"},
        submitted_by_actor_id=executor["id"],
        parent_delivery_id=delivery1["id"],
    )
    assert delivery1["version_no"] == 1 and delivery2["version_no"] == 2
    assert review["status"] == "REVISION_REQUIRED"
