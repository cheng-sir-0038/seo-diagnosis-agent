"""
测试配置 — pytest fixtures 和 mock 工具。

运行测试::

    uv run pytest -v
    uv run pytest -v --cov=src --cov-report=term-missing
"""

import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema


class TestDatabaseURL(str):
    __test__ = False

    def __repr__(self):
        return make_url(str(self)).render_as_string(hide_password=True)


@pytest.fixture
def pg_url():
    """Isolate each database integration case inside the designated test database."""
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        pytest.skip("PostgreSQL 集成测试需要 TEST_DATABASE_URL 指向专用测试库")
    url = make_url(configured)
    if url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL 必须使用 PostgreSQL")
    schema = f"agent_test_{uuid4().hex}"
    admin = create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        options = f"{url.query.get('options', '')} -csearch_path={schema}".strip()
        scoped = url.update_query_dict({"options": options})
        yield TestDatabaseURL(scoped.render_as_string(hide_password=False))
    finally:
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


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
