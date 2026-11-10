from __future__ import annotations

import pytest

from src.agent.content_sanitizer import sanitize_assistant_content


@pytest.mark.parametrize(
    "markup",
    [
        (
            "<｜｜DSML｜｜tool_calls>\n"
            '<｜｜DSML｜｜invoke name="seo_search_metrics">x</｜｜DSML｜｜invoke>\n'
            "</｜｜DSML｜｜tool_calls>"
        ),
        (
            "<|DSML|tool_calls>"
            '<|DSML|invoke name="seo_search_metrics">x</|DSML|invoke>'
            "</|DSML|tool_calls>"
        ),
        "< | | DSML | | tool_calls>hidden< / | | DSML | | tool_calls>",
    ],
)
def test_sanitizer_removes_dsml_tool_control_blocks(markup: str) -> None:
    result = sanitize_assistant_content(f"已确认结论。\n\n{markup}\n\n结束。")

    assert result.had_control_markup is True
    assert "DSML" not in result.content
    assert "seo_search_metrics" not in result.content
    assert result.content == "已确认结论。\n\n结束。"


def test_sanitizer_uses_evidence_fallback_when_budget_summary_contains_dsml() -> None:
    result = sanitize_assistant_content(
        (
            "检测到重复工具调用，已停止继续补查；以下结论基于已经获得的证据。\n\n"
            "好的，我继续查询。\n\n"
            "<｜｜DSML｜｜tool_calls>"
            '<｜｜DSML｜｜invoke name="seo_keyword_overview"></｜｜DSML｜｜invoke>'
            "</｜｜DSML｜｜tool_calls>"
        ),
        fallback="已确认事实：当前已有关键词明细。",
        discard_body_on_control_markup=True,
    )

    assert result.had_control_markup is True
    assert "seo_keyword_overview" not in result.content
    assert "好的，我继续查询" not in result.content
    assert result.content == "已确认事实：当前已有关键词明细。"
