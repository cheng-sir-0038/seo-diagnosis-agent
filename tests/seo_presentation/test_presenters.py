from src.models.common import Capability
from src.seo_presentation.field_catalog import FIELD_CATALOG
from src.seo_presentation.registry import present_capability


def response(data):
    return {
        "meta": {"capability": "organic_keywords", "source": "dataforseo"},
        "data": data,
    }


def test_organic_keywords_uses_versioned_capability_contract() -> None:
    result = present_capability(
        Capability.ORGANIC_KEYWORDS,
        response(
            [
                {
                    "source": "dataforseo",
                    "fetched_at": "2026-07-24T00:00:00Z",
                    "is_estimated": True,
                    "integrity": "complete",
                    "keyword": "seo agent",
                    "position": 7,
                    "previous_position": 11,
                    "position_change": 4,
                    "search_volume": 140,
                    "keyword_difficulty": 42,
                    "cpc": None,
                    "traffic_estimate": 43,
                    "url": "https://example.com/seo-agent",
                    "intent": "commercial",
                    "serp_features": ["people_also_ask"],
                }
            ]
        ),
    )

    assert result is not None
    assert result["schema_version"] == "1.0"
    assert result["capability"] == "organic_keywords"
    assert result["view_type"] == "table"
    assert result["title"]["fallback"] == "关键词明细"
    assert result["description"]["fallback"].startswith("查看具体关键词")
    assert result["provenance"]["source"] == "dataforseo"
    assert result["provenance"]["is_estimated"] is True
    assert isinstance(result["limitations"], list)

    columns = result["columns"]
    assert [column["key"] for column in columns] == [
        "keyword",
        "position",
        "position_change",
        "search_volume",
        "traffic_estimate",
        "keyword_difficulty",
        "intent",
        "url",
    ]
    labels = {column["key"]: column["label"]["fallback"] for column in columns}
    assert labels == {
        "keyword": "关键词",
        "position": "当前排名",
        "position_change": "排名变化",
        "search_volume": "月均搜索量",
        "traffic_estimate": "预估流量",
        "keyword_difficulty": "关键词难度",
        "intent": "搜索意图",
        "url": "排名页面",
    }
    assert (
        next(column for column in columns if column["key"] == "position_change")["formatter"]
        == "rank_delta"
    )
    assert (
        next(column for column in columns if column["key"] == "intent")["value_map"]["commercial"]
        == "商业调研"
    )
    assert next(column for column in columns if column["key"] == "url")["formatter"] == (
        "compact_url"
    )


def test_search_metrics_keeps_specialized_overview_inside_common_contract() -> None:
    result = present_capability(
        Capability.SEARCH_METRICS,
        {
            "meta": {"capability": "search_metrics", "source": "dataforseo"},
            "data": {
                "source": "dataforseo",
                "is_estimated": True,
                "integrity": "complete",
                "country": {"code": "us"},
                "organic_traffic_estimate": 52000,
                "organic_keywords_count": 214,
                "top_3_count": 43,
                "top_10_count": 80,
            },
        },
    )

    assert result is not None
    assert result["schema_version"] == "1.0"
    assert result["capability"] == "search_metrics"
    assert result["view_type"] == "chart"
    assert result["variant"] == "seo_search_overview"
    assert [item["value"] for item in result["distribution"]] == [43, 37, 134]
    assert result["provenance"]["country"] == "US"


def test_unregistered_capability_has_no_formal_presentation() -> None:
    assert present_capability(Capability.SUBSCRIPTION_INFO, response({"plan": "demo"})) is None


def test_gsc_average_position_has_distinct_future_semantics() -> None:
    exact_rank = FIELD_CATALOG["position"]
    average_rank = FIELD_CATALOG["average_position"]

    assert exact_rank.semantic_type == "rank"
    assert average_rank.semantic_type == "average_rank"
    assert average_rank.label == "平均排名"
    assert "不是单次精确排名" in (average_rank.help_text or "")


def test_autocomplete_has_formal_table_contract_without_metric_claims() -> None:
    result = present_capability(
        Capability.AUTOCOMPLETE,
        {
            "meta": {"capability": "autocomplete", "source": "serper"},
            "data": [
                {
                    "source": "serper",
                    "fetched_at": "2026-07-28T00:00:00Z",
                    "is_estimated": False,
                    "integrity": "complete",
                    "position": 1,
                    "value": "seo agent tools",
                    "normalized_value": "seo agent tools",
                }
            ],
        },
    )

    assert result is not None
    assert result["view_type"] == "table"
    assert [column["key"] for column in result["columns"]] == [
        "position",
        "value",
    ]
    assert any("不代表搜索量" in item for item in result["limitations"])
    assert result["rows"] == [{"position": 1, "value": "seo agent tools"}]
