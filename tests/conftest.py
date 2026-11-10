"""
测试配置 — pytest fixtures 和 mock 工具。

运行测试::

    uv run pytest -v
    uv run pytest -v --cov=src --cov-report=term-missing
"""

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_ahrefs_client():
    """创建 AhrefsClient 的 mock 实例，用于 Provider 单元测试。"""
    client = AsyncMock()
    client.request = AsyncMock(return_value={"status": "ok"})
    client.startup = AsyncMock()
    client.shutdown = AsyncMock()
    return client


@pytest.fixture
def sample_site_id():
    """测试用的站点 ID。"""
    return "example.com"


@pytest.fixture
def approve_opportunity():
    async def approve(database, site_id, opportunity_id, actor_id):
        from src.seo_agent.approvals.service import ApprovalService

        service = ApprovalService(database)
        action = await service.create_action(
            site_id=site_id, action_type="ADD_OPPORTUNITY_TO_PRODUCTION", risk_level="HIGH",
            target_type="opportunity", target_id=opportunity_id, requested_payload={},
            approver_actor_id=actor_id, idempotency_key=f"fixture-approval:{opportunity_id}",
        )
        await service.decide(action_id=action["id"], actor_id=actor_id, decision="APPROVE")

    return approve
