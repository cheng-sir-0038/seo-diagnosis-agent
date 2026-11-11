from __future__ import annotations

import asyncio
import json
import re
import time
from html import escape
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from src.agent.models import HumanInputRequest
from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.models.page_suggestions import PageFactCitation, PageSeoDraft, PageSuggestionInput
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.workflows.execution import WorkflowSteps
from src.seo_agent.workflows.structured_model import StructuredModelRunner

FACT_FIELDS = {
    "title",
    "meta_description",
    "meta_keywords",
    "requested_url",
    "final_url",
    "canonical_url",
    "h1s",
    "content_excerpt",
    "html_lang",
    "og_title",
    "og_description",
    "og_image",
    "meta_tags",
    "user_supplied",
}


class PageSuggestionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str | None = None
    force_refresh: bool = True


def _include_input_request(result: ToolResult) -> ToolResult:
    if result.data.get("questions"):
        result.metadata["human_input"] = HumanInputRequest(
            questions=result.data["questions"],
            allow_use_available=result.data.get("proposal") is not None,
        ).model_dump(mode="json")
    return result


def _pointer(value: Any, path: str) -> Any:
    if not path.startswith("/"):
        raise ValueError("证据引用路径无效")
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list) and not re.fullmatch(r"0|[1-9]\d*", key):
                raise ValueError("证据列表索引无效")
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("证据引用不存在") from exc
    return value


def _check_citation(page: dict[str, Any], citation: PageFactCitation) -> str:
    if (
        not citation.source_path.startswith("/")
        or citation.source_path.split("/")[1] not in FACT_FIELDS
    ):
        raise ValueError("引用不是本次页面的内容事实")
    text = _pointer(page, citation.source_path)
    if not isinstance(text, str) or citation.quote not in text:
        raise ValueError("引用原文与页面不一致")
    return citation.quote


def _http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not (parsed.username or parsed.password)
    )


def validate_page_draft(
    page: dict[str, Any], draft: PageSeoDraft, identity: str
) -> dict[str, Any] | None:
    for citation in draft.citations:
        _check_citation(page, citation)
    if draft.image_url is not None:
        citation = draft.image_citation
        if citation is None or not _http_url(draft.image_url):
            raise ValueError("分享图片缺少有效来源")
        quote = _check_citation(page, citation)
        if draft.image_url not in quote or (
            draft.image_url != page.get("og_image")
            and not citation.source_path.startswith("/user_supplied/")
        ):
            raise ValueError("分享图片不是页面原值或用户明确提供的地址")
    elif draft.image_citation is not None:
        raise ValueError("图片引用没有对应图片")
    if draft.schema_type is None:
        if draft.json_ld is not None or draft.json_ld_citations:
            raise ValueError("未确认类型时不能生成结构化数据")
        return None
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]{1,79}", draft.schema_type) or draft.json_ld is None:
        raise ValueError("结构化类型未受支持或缺少内容")
    document = draft.json_ld.model_dump(mode="json", by_alias=True)
    if not isinstance(document, dict) or document.get("@context") != "https://schema.org":
        raise ValueError("JSON-LD 必须使用 Schema.org 对象")
    if document.get("@type") != draft.schema_type:
        raise ValueError("结构化数据类型与页面判断不一致")
    if document.get("url") not in {identity, page.get("final_url")} or not (
        document.get("name") or document.get("headline")
    ):
        raise ValueError("JSON-LD 缺少当前页面身份或名称")
    citations = {item.json_pointer: item for item in draft.json_ld_citations}
    if len(citations) != len(draft.json_ld_citations):
        raise ValueError("结构化数据引用重复")
    used: set[str] = set()
    for path, citation in citations.items():
        _pointer(document, path)
        _check_citation(page, citation)

    def check(value: Any, path: str, depth: int = 0) -> None:
        if depth > 8:
            raise ValueError("JSON-LD 嵌套过深")
        if isinstance(value, dict):
            if not value or any(
                not re.fullmatch(r"@context|@type|@id|[a-z][A-Za-z0-9]*", key) for key in value
            ):
                raise ValueError("JSON-LD 属性格式无效")
            for key, item in value.items():
                if key == "@context" and path:
                    raise ValueError("仅允许根对象声明 Schema.org 上下文")
                check(item, f"{path}/{key}", depth + 1)
            return
        if isinstance(value, list):
            if not value or len(value) > 10:
                raise ValueError("JSON-LD 数组超出建议范围")
            for index, item in enumerate(value):
                check(item, f"{path}/{index}", depth + 1)
            return
        key = path.rsplit("/", 1)[-1]
        if path == "/@context" or (
            key == "@type"
            and isinstance(value, str)
            and re.fullmatch(r"[A-Z][A-Za-z0-9]{1,79}", value)
        ):
            return
        if key == "@id" and isinstance(value, str) and value.split("#", 1)[0] == identity:
            return
        if path == "/url" and value in {identity, page.get("final_url")}:
            return
        if path == "/inLanguage" and value == page.get("html_lang"):
            return
        if path in {"/name", "/headline", "/description"} and value == (
            draft.description if path == "/description" else draft.title
        ):
            return
        citation = citations.get(path)
        if citation is None:
            raise ValueError("结构化数据字段缺少来源")
        quote = _check_citation(page, citation)
        if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float)):
            raise ValueError("结构化数据字段值无效")
        if not str(value).strip() or str(value) not in quote:
            raise ValueError("结构化数据字段值不受引用支持")
        if key in {"url", "image", "logo", "mainEntityOfPage"} and not _http_url(value):
            raise ValueError("结构化数据 URL 无效")
        if isinstance(value, (int, float)) and not re.search(
            rf"(?<![\d.]){re.escape(str(value))}(?![\d.])", quote
        ):
            raise ValueError("结构化数据数字不匹配")
        if (
            _http_url(value)
            and value not in {identity, page.get("final_url"), page.get("og_image")}
            and not citation.source_path.startswith("/user_supplied/")
        ):
            raise ValueError("结构化数据 URL 不在本次已确认范围")
        used.add(path)

    check(document, "")
    automatic = {
        "/name",
        "/headline",
        "/description",
        "/url",
        "/inLanguage",
        "/@type",
        "/@context",
        "/@id",
    }
    if set(citations) - used - automatic:
        raise ValueError("结构化数据引用没有对应字段")
    return document


class PageSuggestionsWorkflow(AgentTool):
    name = "seo_page_suggestions"
    description = (
        "根据当前页面实际内容和业务类型，拟定 TDK、OG 和 JSON-LD 建议及待补信息。"
        "仅生成供人工确认的草稿，不修改网页、Canonical、审批或发布。"
    )
    render_hint = "cards"
    parameters_schema = PageSuggestionArguments.model_json_schema()

    def __init__(
        self,
        page: ToolProtocol,
        runner: StructuredModelRunner,
        timeout: float,
        *,
        observability: ObservabilityClient | None = None,
        user_messages: list[str] | None = None,
    ) -> None:
        self.page, self.runner, self.timeout = page, runner, timeout
        self.observability = observability
        self.user_messages = [message[:2000] for message in (user_messages or [])[-5:]]

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None
    ) -> ToolResult:
        options = PageSuggestionArguments.model_validate(arguments)
        steps = WorkflowSteps(
            {self.page.name: self.page}, self.timeout, self.observability, runtime
        )
        resume = runtime.metadata.get("human_input_resume") if runtime else None
        prior = (
            (resume or {}).get("tool_result", {}).get("data", {}).get("evidence", {}).get("page")
        )
        prior_url = (prior or {}).get("raw", {}).get("data", {}).get("requested_url")
        same_target = resume and options.url in {resume["tool_arguments"].get("url"), prior_url}
        if prior and same_target:
            async with observed_operation(
                self.observability,
                "restore_page_suggestion_facts",
                kind=SpanKind.INTERNAL,
                input_data={"request_id": resume["request_id"], "url": prior_url},
            ) as span:
                payload = steps.evidence["page"] = prior
                steps.coverage["page"] = {"status": "complete", "reused_from_checkpoint": True}
                if span:
                    span.output_data = prior
        else:
            payload = await steps.run("page", self.page.name, options.model_dump(exclude_none=True))
        limitations = [
            "建议仅供人工确认，不修改网站或 Canonical；页面类型为基于已读内容的判断。",
            "源 HTML 不执行 JavaScript，正文仅保留前 6000 字符，现有 JSON-LD 不代表事实已验证。",
            "已校验 JSON 结构与引用；未运行 Schema.org 词汇全量验证或 Google 富结果测试。",
            "Meta Keywords 仅作为主题建议；Google Search 不使用该标签参与收录和排名。",
        ]
        page = (payload or {}).get("raw", {}).get("data")
        incomplete = (
            not isinstance(page, dict)
            or not isinstance(page.get("status_code"), int)
            or not 200 <= page["status_code"] < 300
            or not _http_url(page.get("final_url"))
            or not page.get("content_excerpt")
        )
        supplied_after_pause = bool(resume and resume.get("answer"))
        if incomplete:
            steps.coverage["page"]["status"] = "partial"
        if incomplete and not (
            supplied_after_pause and isinstance(page, dict) and _http_url(page.get("final_url"))
        ):
            return _include_input_request(
                steps.finish(
                    "page_suggestions",
                    "页面 SEO 信息建议",
                    [],
                    limitations,
                    suggestion_status="awaiting_user_input",
                    proposal=None,
                    questions=["目前没有取得页面正文，请补充页面主要内容或可访问的页面地址。"],
                )
            )
        if incomplete:
            limitations.append("页面正文尚未核实，本次建议使用用户补充，落地前需核对页面实际内容。")
        page = {**page, "user_supplied": self.user_messages}
        identity = (
            page.get("canonical_url") if _http_url(page.get("canonical_url")) else page["final_url"]
        )
        steps.coverage["draft"] = {"status": "failed"}
        try:
            async with asyncio.timeout(max(0, steps.deadline - time.monotonic())):
                result = await self.runner.invoke(
                    prompt_key="seo.page.suggestions",
                    method="json_mode",
                    schema=PageSeoDraft,
                    context=PageSuggestionInput(
                        page=page,
                        page_identity_url=identity,
                    ).model_dump(mode="json"),
                    operation="page_seo_suggestions",
                    business_object={
                        "url": page["final_url"],
                        "site_id": runtime.site_id if runtime else None,
                    },
                )
            draft = result.output
            steps.coverage["draft"]["status"] = "complete"
            steps.coverage["validation"] = {"status": "failed"}
            async with observed_operation(
                self.observability,
                "validate_page_suggestions",
                kind=SpanKind.INTERNAL,
                input_data={"draft": draft, "page": page},
            ) as span:
                document = validate_page_draft(page, draft, identity)
                if span:
                    span.output_data = {"valid": True, "schema_type": draft.schema_type}
            steps.coverage["validation"]["status"] = "complete"
        except Exception:
            return steps.finish(
                "page_suggestions",
                "页面 SEO 信息建议",
                [
                    {
                        "key": "unavailable",
                        "title": "建议暂未生成",
                        "summary": "模型调用未完成或建议未通过事实校验，已保留页面证据。",
                    }
                ],
                limitations,
                suggestion_status="needs_review",
                proposal=None,
            )
        missing = list(draft.missing_information)
        questions = list(draft.questions)
        use_available = bool(resume and resume.get("action") == "use_available")
        if use_available:
            missing.extend(questions)
            questions = []
        if document is None and not questions and not use_available:
            questions.append("请补充这个页面的主要用途和希望用户在这里完成的事情。")
        image_url = draft.image_url or page.get("og_image")
        if not _http_url(image_url):
            missing.append("缺少已确认的分享主图，未生成 og:image。")
        if page.get("declared_noindex"):
            missing.append("页面当前声明 noindex，需要人工确认；本建议不改变索引设置。")
        if page.get("existing_json_ld"):
            missing.append("页面已有 JSON-LD，落地前需与现有实体合并，避免直接叠加或覆盖。")
        if any(
            citation.source_path.startswith("/user_supplied/")
            for citation in [
                *draft.citations,
                *draft.json_ld_citations,
                *([draft.image_citation] if draft.image_citation else []),
            ]
        ):
            missing.append(
                "建议使用了用户补充的信息，尚未核实是否已在网页展示，落地前须核对页面一致性。"
            )
        og = {
            "og:title": draft.title,
            "og:description": draft.description,
            "og:type": "article"
            if draft.schema_type in {"Article", "BlogPosting", "NewsArticle"}
            else "website",
            "og:url": identity,
        }
        if _http_url(image_url):
            og["og:image"] = image_url
        json_ld = (
            json.dumps(document, ensure_ascii=False, indent=2).replace("<", "\\u003c")
            if document
            else None
        )
        keywords = escape(", ".join(draft.keywords), quote=True)
        snippet = f"<title>{escape(draft.title)}</title>\n" + "\n".join(
            [
                f'<meta name="description" content="{escape(draft.description, quote=True)}">',
                *([f'<meta name="keywords" content="{keywords}">'] if draft.keywords else []),
                *(
                    f'<meta property="{key}" content="{escape(value, quote=True)}">'
                    for key, value in og.items()
                ),
                *(
                    [f'<script type="application/ld+json">\n{json_ld}\n</script>']
                    if json_ld
                    else []
                ),
            ]
        )
        return _include_input_request(
            steps.finish(
                "page_suggestions",
                "页面 SEO 信息建议",
                [
                    {
                        "key": "type",
                        "title": "页面判断",
                        "summary": draft.page_purpose,
                        "metadata": [
                            {"label": "建议类型", "value": draft.schema_type or "待补充信息"}
                        ],
                    },
                    {
                        "key": "tdk",
                        "title": "TDK 文案建议",
                        "metadata": [
                            {"label": "Title", "value": draft.title},
                            {"label": "Description", "value": draft.description},
                            {"label": "主题词", "value": "、".join(draft.keywords) or "未建议"},
                        ],
                    },
                    {
                        "key": "og",
                        "title": "分享信息建议",
                        "metadata": [{"label": key, "value": value} for key, value in og.items()],
                    },
                    *(
                        [{"key": "json_ld", "title": "JSON-LD 建议", "code": json_ld}]
                        if json_ld
                        else []
                    ),
                    {
                        "key": "evidence",
                        "title": "建议依据",
                        "summary": "\n".join(
                            (
                                "用户补充："
                                if citation.source_path.startswith("/user_supplied/")
                                else "页面原文："
                            )
                            + citation.quote
                            for citation in draft.citations
                        ),
                    },
                    {
                        "key": "missing",
                        "title": "人工确认",
                        "summary": "\n".join(missing) or "请确认页面类型和文案准确后再落地。",
                    },
                    *(
                        [{"key": "questions", "title": "请补充", "summary": "\n".join(questions)}]
                        if questions
                        else []
                    ),
                ],
                limitations,
                suggestion_status="awaiting_user_input" if questions else "draft",
                variant="page-seo-suggestions",
                questions=questions,
                proposal={
                    **draft.model_dump(mode="json", by_alias=True),
                    "json_ld": document,
                    "open_graph": og,
                    "html_snippet": snippet,
                    "missing_information": missing,
                    "canonical_observed": page.get("canonical_url"),
                    "requires_human_review": True,
                    "user_supplied": self.user_messages,
                    "questions": questions,
                },
            )
        )
