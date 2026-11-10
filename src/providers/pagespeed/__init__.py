"""Google PageSpeed Insights Provider。"""

from src.providers.pagespeed.client import PageSpeedClient
from src.providers.pagespeed.config import PageSpeedConfig
from src.providers.pagespeed.provider import PageSpeedProvider

__all__ = ["PageSpeedClient", "PageSpeedConfig", "PageSpeedProvider"]
