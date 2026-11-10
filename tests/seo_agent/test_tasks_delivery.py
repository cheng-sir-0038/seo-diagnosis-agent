from __future__ import annotations

# ruff: noqa: E501
from pathlib import Path

import pytest

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
            "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, opportunity_type, target_country, target_language, title, current_score, priority_level, status, policy_version, first_seen_at, last_seen_at, created_at, updated_at) VALUES (?, ?, ?, ?, 'CONTENT_GAP', 'US', 'en', ?, 85, 'HIGH', 'APPROVED', 'v1', ?, ?, ?, ?)",
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
