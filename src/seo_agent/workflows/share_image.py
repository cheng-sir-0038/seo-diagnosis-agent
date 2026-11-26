from __future__ import annotations

import asyncio
import time
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.models import AgentRun, HumanInputRequest
from src.agent.tools.base import AgentTool, RenderHint, ToolProtocol, ToolResult, ToolRuntimeContext
from src.core.exceptions import ErrorCode, InternalAdapterError
from src.models.common import DataSource
from src.models.page_metadata import PageMetadataReport
from src.models.share_image import ShareImageBrief
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind, Status
from src.seo_agent.workflows.cloudflare_images import MODEL, CloudflareShareImages, ShareImageAsset
from src.seo_agent.workflows.execution import WorkflowSteps
from src.seo_agent.workflows.page_suggestions import share_image_handoff
from src.seo_agent.workflows.structured_model import StructuredModelRunner


class ShareImageArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    url: str | None = Field(
        default=None, min_length=1, max_length=2048, description="本站页面 URL，省略时使用首页。"
    )


class ShareImageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    width: Literal[1216] = 1216
    height: Literal[640] = 640
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
        "为本站页面生成分享图。根据页面内容选取文案和画面主体，配置生图服务时返回实际图片。"
        "页面缺少标题时请用户补充。"
    )
    parameters_schema = ShareImageArguments.model_json_schema()

    def __init__(
        self, page: ToolProtocol, timeout: float, *,
        observability: ObservabilityClient | None = None,
        model_runner: StructuredModelRunner | None = None,
        user_messages: list[str] | None = None,
        image_generator: CloudflareShareImages | None = None,
    ) -> None:
        self.page, self.timeout, self.observability = page, timeout, observability
        self.model_runner = model_runner
        self.user_request = user_messages[-1][:1500] if user_messages else ""
        self.image_generator = image_generator

    def resolve_render_hint(self, result_payload: dict[str, Any]) -> RenderHint | None:
        return "cards" if result_payload["data"].get("image_status") == "ready" else None

    def planned_runtime_metadata(self, run: AgentRun) -> dict[str, Any]:
        return {"page_suggestion": share_image_handoff(run)}

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None
    ) -> ToolResult:
        options = ShareImageArguments.model_validate(arguments)
        steps = WorkflowSteps(
            {self.page.name: self.page}, self.timeout, self.observability, runtime
        )
        resume = runtime.metadata.get("human_input_resume") if runtime else None
        handoff = runtime.metadata.get("page_suggestion") if runtime else None
        proposal = (handoff or {}).get("proposal") or {}
        suggested_page = (handoff or {}).get("evidence", {}).get("page")
        handoff_users = proposal.get("user_supplied") or (handoff or {}).get("user_supplied") or []
        prior = (
            (resume or {}).get("tool_result", {}).get("data", {}).get("evidence", {}).get("page")
        )
        if handoff is not None:
            payload = suggested_page
            if payload is not None:
                steps.evidence["page"] = payload
            steps.coverage["page"] = {
                **handoff.get("coverage", {}).get("page", {}), "reused_from_checkpoint": True,
            }
            user_title = None
        elif prior and resume["tool_arguments"] == arguments:
            payload = steps.evidence["page"] = prior
            steps.coverage["page"] = {"status": "complete", "reused_from_checkpoint": True}
            user_title = resume["answer"]
        else:
            payload = await steps.run(
                "page", self.page.name,
                {**options.model_dump(exclude_none=True), "force_refresh": True},
            )
            user_title = None

        limitations = ["分享图由当前应用保存，供人工预览。"]
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
            if payload is not None or handoff is not None:
                steps.coverage["page"].update(status="partial", error_code="INCOMPLETE_PAGE_HTML")
            return steps.finish("share_image", "页面分享图", [{
                "key": "page", "title": "未取得完整页面",
                "summary": (
                    "没有足够的页面证据生成分享图。" if handoff is not None
                    else "请提供当前站点内可公开访问的页面地址。"
                ),
            }], limitations, image_status="not_generated")

        title_source, title = _first_text(
            ("已校验文案草稿", proposal.get("title")),
            ("og:title", page.og_title), ("Title", page.title),
            ("H1", next(iter(page.h1s), None)), ("用户补充", user_title), limit=160,
        )
        if title is None:
            question = "这页没有可用标题。图上应该写什么标题？"
            steps.coverage["compose"] = {"status": "awaiting_input"}
            result = steps.finish("share_image", "页面分享图", [{
                "key": "title", "title": "页面缺少图片标题", "url": page.final_url,
                "summary": question,
            }], limitations, image_status="not_generated")
            if handoff is None:
                result.metadata["human_input"] = HumanInputRequest(
                    questions=[question],
                ).model_dump(mode="json")
            return result

        description_source, description = _first_text(
            ("已校验文案草稿", proposal.get("description")),
            ("og:description", page.og_description),
            ("Meta Description", page.meta_description), limit=240,
        )
        visual_status, prompt = await self._visual_brief(
            steps, page.final_url,
            {
                "title": title,
                "description": description or "",
                "excerpt": page.content_excerpt[:1000],
            },
            user_request="\n".join(handoff_users)[-3000:] if handoff_users else self.user_request,
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
            )
            if span:
                span.output_data = draft.model_dump(mode="json")
        steps.coverage["compose"] = {"status": "complete"}
        image_status, asset = await self._generate(steps, prompt, draft)
        metadata = [{"label": "标题来源", "value": title_source}]
        if description_source:
            metadata.append({"label": "描述来源", "value": description_source})
        card = {
            "key": "image", "title": title, "summary": description, "url": page.final_url,
            "share_image": draft.model_dump(mode="json"), "metadata": metadata,
        }
        if asset is not None:
            card["image_url"] = asset["image_url"]
            steps.evidence["image"] = {"asset": asset, "usage": "preview_only"}
        return steps.finish(
            "share_image", "页面分享图", [card], limitations, variant="share-image",
            visual_status=visual_status, image_status=image_status,
        )

    async def _visual_brief(
        self, steps: WorkflowSteps, url: str, evidence: dict[str, str], *, user_request: str,
    ) -> tuple[str, str | None]:
        if self.model_runner is None:
            return "not_configured", None
        step = steps.coverage["visual"] = {"status": "failed"}
        try:
            async with asyncio.timeout(max(0, steps.deadline - time.monotonic())):
                result = await self.model_runner.invoke(
                    prompt_key="seo.share_image.visual",
                    method="json_mode",
                    schema=ShareImageBrief,
                    context={**evidence, "user_request": user_request},
                    operation="share_image_visual_brief",
                    business_object={"url": url},
                )
        except Exception:
            return "unavailable", None
        visual = result.output.visual
        sources = [*evidence.values(), user_request]
        if visual is None or not any(visual.evidence_quote in source for source in sources):
            step["status"] = "needs_subject"
            return "needs_subject", None
        step["status"] = "complete"
        return "ready", (
            "Create a landscape editorial image for a website share card. "
            "Keep the subject's stated color, material and transparency accurate.\n"
            f"Focal subject: {visual.subject}. Scene: {visual.scene}.\n"
            f"Composition: {visual.composition}.\n"
            f"Ambient palette and lighting: {visual.tone}. Apply the palette to the surroundings. "
            "Keep the subject's own colors distinct from the ambient palette.\n"
            f"Avoid: {visual.avoid}. Render the scene without lettering or logos."
        )

    async def _generate(
        self, steps: WorkflowSteps, prompt: str | None, draft: ShareImageDraft,
    ) -> tuple[str, ShareImageAsset | None]:
        generator = self.image_generator
        if generator is None or not generator.configured:
            steps.coverage["image"] = {"status": "unavailable", "error_code": "NOT_CONFIGURED"}
            return "not_configured", None
        if prompt is None:
            steps.coverage["image"] = {"status": "unavailable", "error_code": "NO_VISUAL_BRIEF"}
            return "not_generated", None

        dimensions = {"width": draft.width, "height": draft.height}
        input_data = {"prompt": prompt, **dimensions}
        component = "cloudflare_workers_ai"
        async with observed_operation(
            self.observability, "generate_share_image", kind=SpanKind.MODEL,
            component_name=component, input_data={"model": MODEL, **dimensions},
        ) as span:
            model_call = {
                "provider": component, "model": MODEL, "operation": "cloudflare_image_generate",
                "input_summary": input_data,
            }
            started = time.monotonic()
            try:
                async with asyncio.timeout(max(0, steps.deadline - started)):
                    asset = await generator.generate(prompt, **dimensions)
            except (InternalAdapterError, TimeoutError) as exc:
                code = (
                    exc.error_code if isinstance(exc, InternalAdapterError)
                    else ErrorCode.UPSTREAM_TIMEOUT
                )
                steps.coverage["image"] = {"status": "failed", "error_code": code.value}
                if span:
                    await span.model_call(
                        **model_call, status=Status.FAILED, error_code=code.value,
                        latency_ms=round((time.monotonic() - started) * 1000, 3),
                    )
                    await span.capture_exception(exc, error_code=code.value)
                return "failed", None
            if span:
                span.output_data = asset
                await span.model_call(
                    **model_call, output_summary=asset,
                    latency_ms=round((time.monotonic() - started) * 1000, 3),
                )
        steps.coverage["image"] = {"status": "complete", "model": MODEL}
        return "ready", asset
