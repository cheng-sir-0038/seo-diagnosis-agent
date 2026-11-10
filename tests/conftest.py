"""
测试配置 — pytest fixtures 和 mock 工具。

运行测试::

    uv run pytest -v
    uv run pytest -v --cov=src --cov-report=term-missing
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


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
