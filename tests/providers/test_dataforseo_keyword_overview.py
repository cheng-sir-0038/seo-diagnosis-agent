from unittest.mock import AsyncMock

import pytest

from src.models.common import DataIntegrity
from src.providers.dataforseo.config import ENDPOINTS
from src.providers.dataforseo.provider import DataForSEOProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("keywords", [[], [" "], ["a" * 81], ["word " * 11], ["term"] * 51])
async def test_keyword_overview_rejects_invalid_keywords_before_request(keywords) -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock()
    with pytest.raises(ValueError):
        await provider.get_keyword_overview(keywords)
    provider.client.request.assert_not_called()


@pytest.mark.asyncio
async def test_keyword_overview_uses_labs_contract_and_requested_language() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value={
            "tasks": [
                {
                    "result": [
                        {
                            "items": [
                                {
                                    "keyword": "企业名录",
                                    "keyword_info": {"search_volume": 100, "cpc": 0.0},
                                    "keyword_properties": {"keyword_difficulty": 28},
                                    "search_intent_info": {"main_intent": "commercial"},
                                },
                                {
                                    "keyword": "企业目录",
                                    "keyword_info": None,
                                    "keyword_properties": None,
                                },
                            ]
                        }
                    ]
                }
            ]
        }
    )

    rows = await provider.get_keyword_overview(
        ["企业名录", "企业目录"], country="cn", language="zh"
    )

    call = provider.client.request.call_args
    assert call.args == ("keyword_overview",)
    assert ENDPOINTS["keyword_overview"].path == "/v3/dataforseo_labs/google/keyword_overview/live"
    assert call.kwargs["task_body"]["language_code"] == "zh"
    assert "language_name" not in call.kwargs["task_body"]
    assert rows[0].search_volume == 100 and rows[0].keyword_difficulty == 28
    assert rows[0].cpc == 0 and rows[0].intent == "commercial"
    assert rows[1].search_volume is None and rows[1].keyword_difficulty is None
    assert rows[1].integrity == DataIntegrity.PARTIAL
