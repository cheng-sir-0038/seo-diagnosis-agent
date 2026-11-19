from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from pydantic import Field, ValidationError, model_validator

from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.models.common import DataSource
from src.models.geo import (
    GeoCitationRequest,
    GeoContentDraft,
    GeoContract,
    GeoProvider,
    GeoQuestionSelection,
)
from src.models.page_metadata import PageMetadataReport
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.geo_a2a import GeoCitationClient, GeoPeerError
from src.seo_agent.workflows.execution import (
    ResearchArguments,
    WorkflowSteps,
    evidence_rows,
    research_params,
)
from src.seo_agent.workflows.structured_model import StructuredModelRunner

_PROVIDER_LABEL = {"deepseek": "DeepSeek 联网回答", "openai": "OpenAI API 联网回答"}
_CITATION_LABEL = {
    "cited": "本次回答引用了本站",
    "not_cited": "本次返回的引用中未出现本站",
    "no_citations": "本次搜索答复没有返回引用",
    "not_searched": "本次模型没有发起联网搜索",
    "search_failed": "本次联网搜索失败",
}
_QUESTION = re.compile(
    r"[?？]$|如何|怎么|怎样|为什么|是什么|多少钱|哪个好|吗$|"
    r"^(?:how|what|why|when|where|who|which|can|does|is|are|should)\b",
    re.IGNORECASE,
)


class GeoQuestionArguments(ResearchArguments):
    topic: str = Field(min_length=1, max_length=160)
    url: str | None = Field(default=None, min_length=1, max_length=2048)


class GeoCitationArguments(GeoContract):
    question: str = Field(min_length=1, max_length=240)
    provider: GeoProvider = Field(
        default="deepseek", description="用户指定 OpenAI 时才使用 openai。"
    )


class GeoContentArguments(ResearchArguments):
    topic: str | None = Field(default=None, min_length=1, max_length=160)
    question: str | None = Field(default=None, min_length=1, max_length=240)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    provider: GeoProvider = Field(
        default="deepseek", description="用户指定 OpenAI 时才使用 openai。"
    )

    @model_validator(mode="after")
    def require_question_or_topic(self) -> GeoContentArguments:
        if bool((self.topic or "").strip()) == bool((self.question or "").strip()):
            raise ValueError("请提供一个具体问题或一个选题主题")
        return self


def _page_context(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    try:
        page = PageMetadataReport.model_validate((payload or {}).get("raw", {}).get("data"))
    except ValidationError:
        return None
    if (
        page.source != DataSource.PAGE_HTML
        or not 200 <= page.status_code < 300
        or page.status_code in {204, 205, 206}
        or not page.content_excerpt
    ):
        return None
    return {
        "url": page.final_url,
        "title": page.title,
        "h1": page.h1s[:5],
        "excerpt": page.content_excerpt[:3500],
    }


async def _model_step(
    steps: WorkflowSteps,
    runner: StructuredModelRunner,
    role: str,
    schema: type,
    facts: dict[str, Any],
) -> Any:
    status = steps.coverage[role] = {"status": "complete"}
    async with observed_operation(
        steps.observability,
        f"geo_{role}",
        kind=SpanKind.AGENT,
        component_name=role,
        input_data=facts,
    ) as span:
        try:
            async with asyncio.timeout(max(0, steps.deadline - time.monotonic())):
                result = await runner.invoke(
                    prompt_key=f"seo.geo.{role}",
                    schema=schema,
                    context=facts,
                    method="json_mode",
                    operation=f"geo_{role}",
                    business_object={"application_run_id": steps.runtime.run_id},
                )
            if span:
                span.output_data = result.output.model_dump(mode="json")
            return result.output
        except Exception as exc:
            code = "TOOL_TIMEOUT" if isinstance(exc, TimeoutError) else "GEO_MODEL_OUTPUT_FAILED"
            status.update(status="failed", error_code=code)
            if span:
                await span.capture_exception(RuntimeError(code), error_code=code)
    return None


class GeoQuestionWorkflow(AgentTool):
    name = "seo_geo_questions"
    description = "GEO 选题。查 Google 相关问题和联想词，结合本站页面推荐一个优先回答的真实问题。"
    parameters_schema = GeoQuestionArguments.model_json_schema()
    render_hint = "cards"

    def __init__(
        self,
        tools: dict[str, ToolProtocol],
        runner: StructuredModelRunner,
        timeout: float,
        observability: ObservabilityClient | None,
    ) -> None:
        self.tools, self.runner, self.timeout_seconds = tools, runner, timeout
        self.observability = observability

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = GeoQuestionArguments.model_validate(arguments)
        topic = options.topic.strip()
        if not topic:
            raise ValueError("选题主题不能为空")
        steps = WorkflowSteps(self.tools, self.timeout_seconds, self.observability, runtime)
        params = research_params(options)
        page, related, autocomplete = await asyncio.gather(
            steps.run("page", "seo_page_metadata", {"url": options.url}),
            steps.run(
                "related_questions",
                "seo_serp_overview",
                {
                    **params,
                    "keyword": topic,
                    "limit": 10,
                    "autocorrect": False,
                    "preferred_provider": "serper",
                    "allow_fallback": False,
                    "result_types": ["people_also_ask"],
                },
            ),
            steps.run(
                "autocomplete",
                "seo_autocomplete",
                {
                    **{key: value for key, value in params.items() if key != "limit"},
                    "query": topic,
                },
            ),
        )
        page_info = _page_context(page)
        if page is not None and page_info is None:
            steps.coverage["page"].update(status="partial", error_code="INCOMPLETE_PAGE_HTML")
        questions = _observed_questions(related, autocomplete, options.limit)
        selection = (
            await _model_step(
                steps,
                self.runner,
                "question_researcher",
                GeoQuestionSelection,
                {"topic": topic, "page": page_info, "questions": questions},
            )
            if questions
            else None
        )
        selected = next(
            (item for item in questions if selection and item["id"] == selection.question_id),
            None,
        )
        if selection and selection.question_id is not None and selected is None:
            steps.coverage["question_researcher"].update(
                status="partial",
                error_code="GEO_UNOBSERVED_QUESTION",
            )
        items = [
            {
                "key": f"question-{item['id']}",
                "title": item["question"],
                "summary": selection.reason if selected == item else None,
                "metadata": [
                    {"label": "来源", "value": "、".join(item["sources"])},
                    *([{"label": "建议", "value": "优先核验"}] if selected == item else []),
                ],
            }
            for item in questions
        ] or [
            {
                "key": "questions",
                "title": "本次没有发现可用的问题词",
                "summary": "可以给出一个实际客户会问的问题，直接核验 AI 引用。",
            }
        ]
        return steps.finish(
            "geo_questions",
            "GEO 选题",
            items,
            ["问题来源是 Google 当前搜索样本。优先级由 Agent 结合目标页判断。"],
            topic=topic,
            questions=questions,
            selected_question=selected,
            selection_reason=selection.reason if selection else None,
        )


def _observed_questions(
    related: dict[str, Any] | None,
    autocomplete: dict[str, Any] | None,
    limit: int,
) -> list[dict[str, Any]]:
    observed: dict[str, dict[str, Any]] = {}
    for payload, field, source in (
        (related, "title", "Google 相关问题"),
        (autocomplete, "value", "Google 联想词"),
    ):
        for row in evidence_rows(payload):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240:
                continue
            question = value.strip()
            if field == "title" and row.get("type") != "people_also_ask":
                continue
            if field == "value" and not _QUESTION.search(question):
                continue
            key = re.sub(r"\s+", " ", question.casefold()).rstrip("?？").rstrip()
            if key not in observed and len(observed) >= limit:
                continue
            result = observed.setdefault(
                key,
                {
                    "id": len(observed) + 1,
                    "question": question,
                    "sources": [],
                },
            )
            if source not in result["sources"]:
                result["sources"].append(source)
    return list(observed.values())


class GeoCitationWorkflow(AgentTool):
    name = "seo_geo_citations"
    description = (
        "核验一个确切问题在指定 AI 联网回答中是否引用本站。默认 DeepSeek，按需使用 OpenAI API。"
    )
    parameters_schema = GeoCitationArguments.model_json_schema()
    render_hint = "cards"

    def __init__(self, client: GeoCitationClient, domain: str, timeout: float) -> None:
        self.client, self.domain, self.timeout_seconds = client, domain, timeout

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = GeoCitationArguments.model_validate(arguments)
        request = GeoCitationRequest(**options.model_dump(), target_domain=self.domain)
        try:
            report, task_id = await self.client.verify(request)
        except GeoPeerError as exc:
            return ToolResult(
                success=False,
                error="未取得所选来源的联网引用样本。",
                error_code=exc.error_code,
            )
        span_source = _PROVIDER_LABEL[report.sample.provider]
        status = report.citation_status
        matches = report.matching_urls
        steps = WorkflowSteps({}, self.timeout_seconds, None, runtime)
        steps.coverage["citation_verifier"] = {
            "status": "complete" if status in {"cited", "not_cited"} else "partial",
            "a2a_task_id": task_id,
            "citation_status": status,
        }
        steps.evidence["sample"] = {
            "report": report.model_dump(mode="json"),
            "provenance": {
                "source": report.sample.provider,
                "source_label": span_source,
                "fetched_at": report.sample.fetched_at.isoformat(),
                "is_estimated": False,
            },
        }
        items = [
            {
                "key": "citation-result",
                "title": _CITATION_LABEL[status],
                "summary": request.question,
                "metadata": [
                    {"label": "答案来源", "value": span_source},
                    {"label": "模型", "value": report.sample.model},
                    {"label": "引用链接", "value": str(len(report.sample.citations))},
                ],
            }
        ]
        for index, citation in enumerate(
            sorted(report.sample.citations, key=lambda item: item.url not in matches)[:5],
            start=1,
        ):
            if status not in {"cited", "not_cited"}:
                break
            items.append(
                {
                    "key": f"citation-{index}",
                    "title": citation.title,
                    "url": citation.url,
                    "summary": citation.answer_excerpt or None,
                    "metadata": [{"label": "来源", "value": span_source}],
                }
            )
        return steps.finish(
            "geo_citations",
            "AI 搜索引用核验",
            items,
            [
                "结论只适用于本次 API 联网回答。列表展示最多五条引用链接。",
                "链接旁的片段来自 AI 答案原文，供定位引用。",
            ],
            citation_status=status,
            matching_urls=matches,
            a2a_task_id=task_id,
        )


class GeoContentWorkflow(AgentTool):
    name = "seo_geo_content"
    description = (
        "GEO 内容缺口与改稿。给定具体问题时核验引用后审阅本站页；"
        "给定主题时先发现并选一个真实问题，再查引用并出改稿。仅交付建议。"
    )
    parameters_schema = GeoContentArguments.model_json_schema()
    render_hint = "cards"

    def __init__(
        self,
        tools: dict[str, ToolProtocol],
        runner: StructuredModelRunner,
        timeout: float,
        observability: ObservabilityClient | None,
    ) -> None:
        self.tools, self.runner, self.timeout_seconds = tools, runner, timeout
        self.observability = observability

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = GeoContentArguments.model_validate(arguments)
        steps = WorkflowSteps(self.tools, self.timeout_seconds, self.observability, runtime)
        question = (options.question or "").strip()
        items: list[dict[str, Any]] = []
        if options.topic and options.topic.strip():
            research = await steps.run(
                "research",
                "seo_geo_questions",
                {
                    **research_params(options),
                    "topic": options.topic,
                    "url": options.url,
                },
            )
            selected = (research or {}).get("selected_question")
            question = selected["question"] if selected else ""
            page = (research or {}).get("evidence", {}).get("page")
            items.extend((research or {}).get("items", []))
        else:
            page = await steps.run("page", "seo_page_metadata", {"url": options.url})
        page_info = _page_context(page)
        if page is not None and page_info is None and "page" in steps.coverage:
            steps.coverage["page"].update(status="partial", error_code="INCOMPLETE_PAGE_HTML")
        citations = (
            await steps.run(
                "citations",
                "seo_geo_citations",
                {
                    "question": question,
                    "provider": options.provider,
                },
            )
            if question
            else None
        )
        report = None
        if citations:
            items.extend(citations["items"])
            if citations["citation_status"] in {"cited", "not_cited"}:
                report = citations["evidence"]["sample"]["report"]
        edits = []
        if page_info and report:
            sources = [
                {
                    "id": index,
                    "url": citation["url"],
                    "title": citation["title"],
                    "answer_excerpt": citation["answer_excerpt"],
                }
                for index, citation in enumerate(report["sample"]["citations"][:5], start=1)
            ]
            draft = await _model_step(
                steps,
                self.runner,
                "content_editor",
                GeoContentDraft,
                {"question": question, "page": page_info, "citation_evidence": sources},
            )
            if draft:
                edits = [
                    edit
                    for edit in draft.edits
                    if edit.page_quote in page_info["excerpt"] and edit.citation_id <= len(sources)
                ]
                if len(edits) != len(draft.edits):
                    steps.coverage["content_editor"].update(
                        status="partial",
                        error_code="GEO_INVALID_EVIDENCE",
                    )
                for index, edit in enumerate(edits, start=1):
                    source = sources[edit.citation_id - 1]
                    items.append(
                        {
                            "key": f"draft-{index}",
                            "title": edit.gap,
                            "summary": edit.draft,
                            "url": source["url"],
                            "metadata": [
                                {"label": "目标页原文", "value": edit.page_quote},
                                {"label": "AI 答案参考链接", "value": source["title"]},
                            ],
                        }
                    )
                if draft.needs_verification:
                    items.append(
                        {
                            "key": "verification",
                            "title": "建议人工核实",
                            "summary": "\n".join(draft.needs_verification),
                        }
                    )
        else:
            steps.coverage["content_editor"] = {
                "status": "partial",
                "error_code": "GEO_INSUFFICIENT_EVIDENCE",
            }
        if not edits:
            items.append(
                {
                    "key": "no-draft",
                    "title": "本次没有形成有依据的改稿",
                    "summary": "改稿需要一个明确问题、可读的目标页正文和带引用的联网答案。",
                }
            )
        return steps.finish(
            "geo_content",
            "GEO 内容缺口与改稿",
            items,
            ["缺口判断来自本次读取的目标页正文摘要和带引用的 AI 答案，改稿供人工审阅。"],
            question=question or None,
            target_url=page_info["url"] if page_info else None,
            edits=[edit.model_dump(mode="json") for edit in edits],
        )
