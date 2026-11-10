"""DataForSEO Provider — 外部竞争全景数据。

DataForSEO 提供 SERP 数据、关键词研究、域名分析、外链数据等。
按量付费，认证方式为 HTTP Basic Auth。

使用方式::

    from src.providers.dataforseo import DataForSEOProvider

    provider = DataForSEOProvider()
    await provider.startup()
    data = await provider.get_search_metrics(site_id="example.com")
    await provider.shutdown()
"""

from src.providers.dataforseo.client import DataForSEOClient
from src.providers.dataforseo.config import DataForSEOConfig, ENDPOINTS
from src.providers.dataforseo.provider import DataForSEOProvider

__all__ = ["DataForSEOProvider", "DataForSEOClient", "DataForSEOConfig", "ENDPOINTS"]
