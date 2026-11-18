from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.tools.base import ToolRuntimeContext
from src.models.common import Capability, DataSource
from src.models.page_metadata import PageMetadataReport
from src.seo_agent.chat.tools import build_seo_agent_registry
from src.seo_agent.workflows.structured_model import StructuredModelRunner

SITE = {"id": "site_1", "domain": "example.com"}
URL = "https://example.com/coffee/"


class PageSource:
    def __init__(self, **fields: Any) -> None:
        self.fields = fields
        self.calls: list[dict[str, Any]] = []

    def live_capabilities(self) -> set[Capability]:
        return {Capability.PAGE_METADATA}

    async def query_live(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        url = kwargs["params"]["url"]
        page = PageMetadataReport(
            source=DataSource.PAGE_HTML, requested_url=url, final_url=url,
            **{"status_code": 200, "content_length_bytes": 512, **self.fields},
        )
        return {
            "meta": {"capability": "page_metadata", "source": "page_html"},
            "data": page.model_dump(mode="json"),
        }


@pytest.mark.parametrize(
    ("topic", "subject", "evidence", "expected_status"),
    [
        ("手冲咖啡入门", "玻璃滤杯与滤纸", "手冲咖啡", "ready"),
        ("兰花养护指南", "一盆开花的兰花", "兰花", "ready"),
        ("首页", "不明主题的摆件", "兰花", "needs_subject"),
    ],
)
@pytest.mark.asyncio
async def test_share_image_brief_uses_the_current_page(
    topic: str, subject: str, evidence: str, expected_status: str,
) -> None:
    class Model:
        def with_structured_output(self, schema, **kwargs):
            async def ainvoke(messages):
                page = json.loads(messages[-1].content)
                assert page["title"] == topic
                assert page["user_request"] == "为当前页面做一张分享图"
                return schema.model_validate({"visual": {
                    "subject": subject, "scene": "自然光下的静物",
                    "tone": "温暖中性色", "composition": "居中的近景",
                    "avoid": "文字与标识", "evidence_quote": evidence,
                }})
            return SimpleNamespace(ainvoke=ainvoke)

    runner = StructuredModelRunner(model_factory=Model)
    tool = build_seo_agent_registry(
        PageSource(title=topic), SITE, model_runner=runner,
        user_messages=["请在上一个页面画一只帆船", "为当前页面做一张分享图"],
    ).get("seo_share_image")
    result = await tool.execute({"url": URL})

    assert result.data["visual_status"] == expected_status
    assert tool.render_hint is None
    brief_card = next(item for item in result.data["items"] if item["key"] == "visual")
    if expected_status == "ready":
        assert subject in brief_card["code"]
    else:
        assert "code" not in brief_card


@pytest.mark.asyncio
async def test_share_image_uses_page_facts_and_fetches_only_the_requested_page() -> None:
    source = PageSource(
        og_title="  手冲咖啡\n入门 ", title="咖啡教程", meta_description="从选豆到冲煮",
        og_image="https://another.example/untrusted-image.png",
    )
    tool = build_seo_agent_registry(source, SITE).get("seo_share_image")

    result = await tool.execute({"url": URL})

    assert tool.parameters_schema["additionalProperties"] is False
    assert result.data["workflow_status"] == "complete"
    assert result.data["evidence"]["page"]["raw"]["data"]["final_url"] == URL
    card = result.data["items"][0]
    assert card["url"] == URL
    assert card["share_image"] == {
        "width": 1200, "height": 630, "host": "example.com",
        "title": "手冲咖啡 入门", "description": "从选豆到冲煮",
    }
    assert card["metadata"] == [
        {"label": "标题来源", "value": "og:title"},
        {"label": "描述来源", "value": "Meta Description"},
    ]
    assert len(source.calls) == 1
    assert source.calls[0]["capability"] == Capability.PAGE_METADATA
    assert source.calls[0]["params"] == {"url": URL}
    assert source.calls[0]["force_refresh"] is True


@pytest.mark.asyncio
async def test_share_image_waits_for_a_title_and_reuses_page_on_resume() -> None:
    source = PageSource(meta_description="一份入门教程")
    tool = build_seo_agent_registry(source, SITE).get("seo_share_image")
    arguments = {"url": URL}

    waiting = await tool.execute(arguments)

    assert waiting.metadata["human_input"]["questions"] == [
        "这页没有可用标题。图上应该写什么标题？",
    ]
    assert waiting.metadata["human_input"]["allow_use_available"] is False
    assert "share_image" not in waiting.data["items"][0]

    result = await tool.execute(arguments, ToolRuntimeContext(metadata={"human_input_resume": {
        "tool_arguments": arguments, "tool_result": waiting.model_dump(mode="json"),
        "answer": "手冲咖啡入门", "action": "answer",
    }}))

    assert len(source.calls) == 1
    assert result.data["coverage"]["page"]["reused_from_checkpoint"] is True
    assert result.data["workflow_status"] == "complete"
    assert not result.metadata
    assert result.data["items"][0]["share_image"]["title"] == "手冲咖啡入门"
    assert result.data["items"][0]["metadata"][0]["value"] == "用户补充"


@pytest.mark.parametrize("status", [206, 404])
@pytest.mark.asyncio
async def test_share_image_does_not_render_incomplete_html_or_error_pages(status: int) -> None:
    source = PageSource(status_code=status, title="Example error")
    tool = build_seo_agent_registry(source, SITE).get("seo_share_image")

    result = await tool.execute({"url": URL})

    assert result.data["workflow_status"] == "partial"
    assert "share_image" not in result.data["items"][0]
    assert not result.metadata


@pytest.mark.asyncio
async def test_share_image_rejects_another_site_before_fetching() -> None:
    source = PageSource()
    tool = build_seo_agent_registry(source, SITE).get("seo_share_image")

    result = await tool.execute({"url": "https://another.example/"})

    assert not source.calls
    assert result.data["coverage"]["page"]["error_code"] == "INVALID_ARGUMENT"
    assert "share_image" not in result.data["items"][0]
