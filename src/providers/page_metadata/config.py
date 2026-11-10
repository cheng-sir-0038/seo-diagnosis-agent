"""页面 HTML 元信息 Provider 配置。"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.config import settings


@dataclass(frozen=True, slots=True)
class PageMetadataConfig:
    request_timeout: float = settings.page_metadata_request_timeout
    max_response_bytes: int = settings.page_metadata_max_response_bytes
    max_redirects: int = settings.page_metadata_max_redirects
    user_agent: str = settings.page_metadata_user_agent
