from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from src.models.common import Capability
from src.seo_presentation.contracts import base_contract


class SearchMetricsPresenter:
    capability = Capability.SEARCH_METRICS

    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        data = response.get("data")
        if not isinstance(data, Mapping):
            return None
        required = {
            "organic_traffic_estimate",
            "organic_keywords_count",
            "top_3_count",
            "top_10_count",
        }
        if not required.issubset(data):
            return None

        traffic = _nonnegative_number(data.get("organic_traffic_estimate"))
        total = _nonnegative_int(data.get("organic_keywords_count"))
        top_3 = _nonnegative_int(data.get("top_3_count"))
        top_10 = _nonnegative_int(data.get("top_10_count"))
        if None in {traffic, total, top_3, top_10}:
            return None
        assert (
            traffic is not None and total is not None and top_3 is not None and top_10 is not None
        )

        result = base_contract(
            capability=self.capability.value,
            view_type="chart",
            title_key="seo.search_metrics.title",
            title_fallback="自然搜索概览",
            description_key="seo.search_metrics.description",
            description_fallback="查看当前流量、关键词覆盖和排名分布。",
            response=response,
            limitations=["自然流量和关键词数量来自第三方 SEO 数据源，通常属于估算值。"],
        )
        result.update(
            {
                "type": "bar",
                "variant": "seo_search_overview",
                "metrics": [
                    {
                        "key": "organic_traffic",
                        "label": "预估自然流量",
                        "value": traffic,
                        "kind": "estimate",
                        "helper": "第三方平台根据排名和搜索量估算",
                    },
                    {
                        "key": "organic_keywords",
                        "label": "有排名的关键词",
                        "value": total,
                        "kind": "count",
                        "helper": "当前目标市场中获得自然排名的关键词",
                    },
                    {
                        "key": "top_3",
                        "label": "Top 3 关键词",
                        "value": top_3,
                        "kind": "count",
                        "helper": "排名第 1-3 名",
                    },
                    {
                        "key": "top_10",
                        "label": "Top 10 关键词",
                        "value": top_10,
                        "kind": "count",
                        "helper": "累计排名第 1-10 名，包含 Top 3",
                    },
                ],
            }
        )

        distribution: list[dict[str, Any]] = []
        labels: list[str] = []
        values: list[int] = []
        warning: str | None = None
        if top_3 <= top_10 <= total:
            buckets = (
                ("top_3", "排名 1-3", top_3),
                ("top_4_10", "排名 4-10", top_10 - top_3),
                ("outside_top_10", "排名 11+", total - top_10),
            )
            for key, label, value in buckets:
                percentage = round(value / total * 100, 1) if total else 0.0
                distribution.append(
                    {"key": key, "label": label, "value": value, "percentage": percentage}
                )
            labels = [item["label"] for item in distribution]
            values = [item["value"] for item in distribution]
            outside = total - top_10
            middle = top_10 - top_3
            if total == 0:
                insight = "当前没有可用于计算排名分布的关键词。"
            elif middle == 0:
                insight = (
                    f"当前 {top_3} 个关键词进入前 3 名，暂无关键词位于第 4-10 名；"
                    f"{outside} 个仍在前 10 名之外。"
                )
            else:
                insight = (
                    f"当前 {top_3} 个关键词进入前 3 名，{middle} 个位于第 4-10 名，"
                    f"{outside} 个仍在前 10 名之外。"
                )
        else:
            insight = "搜索总览已返回，但排名累计口径存在冲突，暂不绘制排名分布。"
            warning = (
                "排名口径异常：应满足 Top 3 ≤ Top 10 ≤ 关键词总数。"
                f"当前值为 {top_3} ≤ {top_10} ≤ {total}。"
            )

        result.update(
            {
                "labels": labels,
                "datasets": [{"label": "关键词数量", "data": values}] if labels else [],
                "distribution": distribution,
                "insight": insight,
                "warning": warning,
                # 保留旧组件字段，值与统一 provenance 相同。
                "meta": dict(result["provenance"]),
            }
        )
        return result


def _nonnegative_number(value: Any) -> int | float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not math.isfinite(float(value)) or float(value) < 0:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def _nonnegative_int(value: Any) -> int | None:
    number = _nonnegative_number(value)
    return None if number is None else int(round(float(number)))
