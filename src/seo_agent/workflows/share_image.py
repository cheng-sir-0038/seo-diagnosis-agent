from __future__ import annotations

import asyncio
import time
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.models import HumanInputRequest
from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.models.common import DataSource
from src.models.page_metadata import PageMetadataReport
from src.models.share_image import ShareImageBrief
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.workflows.execution import WorkflowSteps
from src.seo_agent.workflows.structured_model import StructuredModelRunner


class ShareImageArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    url: str | None = Field(
        default=None, min_length=1, max_length=2048, description="本站页面 URL，省略时使用首页。"
    )


class ShareImageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    width: Literal[1200] = 1200
    height: Literal[630] = 630
    host: str = Field(min_length=1, max_length=253)
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=240)


def _first_text(*candidates: tuple[str, str | None], limit: int) -> tuple[str, str | None]:
    for source, value in candidates:
        text = " ".join((value or "").split())
        if text:
            return source, text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
    return "", None


class ShareImageWorkflow(AgentTool):
    name = "seo_share_image"
    description = (
        "为本站页面拟定分享图方案。根据页面内容提供文案和可直接复制的绘图提示词。"
        "页面缺少标题时请用户补充。"
    )
    parameters_schema = ShareImageArguments.model_json_schema()

    def __init__(
        self, page: ToolProtocol, timeout: float, *,
        observability: ObservabilityClient | None = None,
        model_runner: StructuredModelRunner | None = None,
        user_messages: list[str] | None = None,
    ) -> None:
        self.page, self.timeout, self.observability = page, timeout, observability
        self.model_runner = model_runner
        self.user_request = user_messages[-1][:1500] if user_messages else ""

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None
    ) -> ToolResult:
        options = ShareImageArguments.model_validate(arguments)
        steps = WorkflowSteps(
            {self.page.name: self.page}, self.timeout, self.observability, runtime
        )
        resume = runtime.metadata.get("human_input_resume") if runtime else None
        prior = (
            (resume or {}).get("tool_result", {}).get("data", {}).get("evidence", {}).get("page")
        )
        if prior and resume["tool_arguments"] == arguments:
            payload = steps.evidence["page"] = prior
            steps.coverage["page"] = {"status": "complete", "reused_from_checkpoint": True}
            user_title = resume["answer"]
        else:
            payload = await steps.run(
                "page", self.page.name,
                {**options.model_dump(exclude_none=True), "force_refresh": True},
            )
            user_title = None

        limitations = ["产出为分享图文案和绘图提示词，供后续绘图使用。"]
        try:
            page = PageMetadataReport.model_validate((payload or {}).get("raw", {}).get("data"))
            url = urlparse(page.final_url)
        except (ValidationError, ValueError):
            page = None
        if (
            page is None or page.source != DataSource.PAGE_HTML
            or not 200 <= page.status_code < 300 or page.status_code in {204, 205, 206}
            or not page.content_length_bytes or url.scheme not in {"http", "https"}
            or not url.hostname
        ):
            if payload is not None:
                steps.coverage["page"].update(status="partial", error_code="INCOMPLETE_PAGE_HTML")
            return steps.finish("share_image", "页面分享图", [{
                "key": "page", "title": "未取得完整页面",
                "summary": "请提供当前站点内可公开访问的页面地址。",
            }], limitations)

        title_source, title = _first_text(
            ("og:title", page.og_title), ("Title", page.title),
            ("H1", next(iter(page.h1s), None)), ("用户补充", user_title), limit=160,
        )
        if title is None:
            question = "这页没有可用标题。图上应该写什么标题？"
            steps.coverage["compose"] = {"status": "awaiting_input"}
            result = steps.finish("share_image", "页面分享图", [{
                "key": "title", "title": "页面缺少图片标题", "url": page.final_url,
                "summary": question,
            }], limitations)
            result.metadata["human_input"] = HumanInputRequest(
                questions=[question],
            ).model_dump(mode="json")
            return result

        description_source, description = _first_text(
            ("og:description", page.og_description),
            ("Meta Description", page.meta_description), limit=240,
        )
        visual_status, visual_card = await self._visual_brief(
            steps, page.final_url,
            {
                "title": title,
                "description": description or "",
                "excerpt": page.content_excerpt[:1000],
            },
        )
        async with observed_operation(
            self.observability, "compose_share_image", kind=SpanKind.INTERNAL,
            component_name="seo_workflow",
            input_data={
                "url": page.final_url, "title_source": title_source,
                "description_source": description_source or None,
                "reused_page": bool(steps.coverage["page"].get("reused_from_checkpoint")),
            },
        ) as span:
            draft = ShareImageDraft(
                host=url.hostname, title=title, description=description,
            ).model_dump(mode="json")
            if span:
                span.output_data = draft
        steps.coverage["compose"] = {"status": "complete"}
        metadata = [{"label": "标题来源", "value": title_source}]
        if description_source:
            metadata.append({"label": "描述来源", "value": description_source})
        items = [{
            "key": "image", "title": "分享图文案", "url": page.final_url,
            "share_image": draft, "metadata": metadata,
        }]
        if visual_card is not None:
            items.append(visual_card)
        return steps.finish(
            "share_image", "页面分享图方案", items, limitations, visual_status=visual_status,
        )

    async def _visual_brief(
        self, steps: WorkflowSteps, url: str, evidence: dict[str, str],
    ) -> tuple[str, dict[str, Any] | None]:
        if self.model_runner is None:
            return "not_configured", None
        step = steps.coverage["visual"] = {"status": "failed"}
        try:
            async with asyncio.timeout(max(0, steps.deadline - time.monotonic())):
                result = await self.model_runner.invoke(
                    prompt_key="seo.share_image.visual",
                    method="json_mode",
                    schema=ShareImageBrief,
                    context={**evidence, "user_request": self.user_request},
                    operation="share_image_visual_brief",
                    business_object={"url": url},
                )
        except Exception:
            return "unavailable", {
                "key": "visual", "title": "绘图提示词暂未生成",
                "summary": "页面证据已保留，可以稍后重试。",
            }
        visual = result.output.visual
        sources = [*evidence.values(), self.user_request]
        if visual is None or not any(visual.evidence_quote in source for source in sources):
            step["status"] = "needs_subject"
            return "needs_subject", {
                "key": "visual", "title": "需要确认画面主体",
                "summary": "你希望主图里出现什么？",
            }
        step["status"] = "complete"
        return "ready", {
            "key": "visual", "title": "主图提示词",
            "summary": f"主题依据「{visual.evidence_quote}」",
            "code": (
                "为网站分享卡片制作一张主图。\n"
                f"画面主体是{visual.subject}。场景为{visual.scene}。\n"
                f"色调和光线采用{visual.tone}。构图为{visual.composition}。\n"
                f"避开{visual.avoid}。画面只保留图像，由卡片单独排版文字。"
            ),
        }
