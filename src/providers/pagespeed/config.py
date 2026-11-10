"""PageSpeed Insights Provider 配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.config import settings


@dataclass(frozen=True, slots=True)
class PageSpeedConfig:
    api_base_url: str = field(default_factory=lambda: settings.pagespeed_api_base_url)
    api_key: str = field(default_factory=lambda: settings.pagespeed_api_key)
    request_timeout: float = field(default_factory=lambda: settings.pagespeed_request_timeout)
    rate_limit_rpm: int = field(default_factory=lambda: settings.pagespeed_rate_limit)
    max_retries: int = field(default_factory=lambda: settings.pagespeed_max_retries)
    default_strategy: str = field(default_factory=lambda: settings.pagespeed_default_strategy)
    default_locale: str = field(default_factory=lambda: settings.pagespeed_default_locale)
    max_opportunities: int = field(default_factory=lambda: settings.pagespeed_max_opportunities)
