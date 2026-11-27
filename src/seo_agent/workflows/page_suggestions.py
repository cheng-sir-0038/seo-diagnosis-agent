from __future__ import annotations

import asyncio
import json
import re
import time
from html import escape
from typing import Any, Literal
from urllib.parse import urlparse

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ConfigDict, ValidationError

from src.agent.models import AgentEvent, AgentRun, HumanInputRequest
from src.agent.providers.base import ToolCall
from src.agent.providers.errors import ModelConfigurationError, ModelRequestError
from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.models.page_suggestions import (
    PageFactCitation,
    PageSeoDraft,
    PageSuggestionInput,
    StructuredFactCitation,
)
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
    "headings",
    "content_excerpt",
    "html_lang",
    "og_title",
    "og_description",
    "og_image",
    "meta_tags",
    "user_supplied",
}


PLANNED_SHARE_IMAGE_ID = "planned_seo_suggestions_share_image"
SHARE_IMAGE_TOOL = "seo_share_image"
_IMAGE_PREVIEW_PATH = "/api/v1/seo-agent/chat/images/"
_CLAUSE = re.compile(r"[，,；;。！？\n]+")
_LINK = re.compile(r"https?://[^\s<>\"'，,；;。！？）)]+", re.I)
_IMAGE = re.compile(
    r"图(?:片|像|地址|链接|$)|(?:分享|主|配|题|OG\s*)图|OG[ _:-]?image|logo|封面|\b(?:image|picture)\b",
    re.I,
)
_GENERATE = re.compile(
    r"生成|制作|创建|绘制|换(?:个|张|一张)|画(?:个|张|一张)|\b(?:generate|create|make|draw)\b",
    re.I,
)
_NEGATION = (
    r"不要|不用|别|无需|不必|不需要|不想|禁止|暂不|先不|不再|不能|不可以|不希望|不|"
    r"\b(?:no|not|don't|do not)\b"
)
_NEGATED_GENERATION = re.compile(
    rf"(?:{_NEGATION})\s*(?:(?:再|去|帮我|给我|请|你|自行|重新|自动|现在)\s*)*{_GENERATE.pattern}",
    re.I,
)
_EXCLUDE_IMAGE = re.compile(rf"{_NEGATION}|跳过|省略|去掉|移除|排除|禁用|\b(?:skip|omit|remove)\b", re.I)
_USE_LINK = re.compile(r"用|选|指定|换成|改成|地址|链接|\b(?:use|link|url)\b", re.I)
_NO_URL = re.compile(r"没有|没(?:有)?找到|暂无|暂缺|未提供|不能提供|不给|\b(?:no|without)\b", re.I)
_OPTIONAL_FIELD = re.compile(
    r"联系(?:电话|方式|地址)|办公地址|公司地址|实体地址|详细地址|地[址點]|电话|手机(?:号)?|邮编|"
    r"作者|编者|日期|发布时间|发布日期|更新时间|发(?:布|表)时间|评分|报价|"
    r"\b(?:author|date|phone|address|postal code|rating)\b",
    re.I,
)
_PAGE_ADDRESS = re.compile(r"页面(?:的)?地址|网页(?:的)?地址|网站(?:的)?地址|page address|page url", re.I)
_SCHEMA_TYPE = re.compile(r"结构化(?:数据)?(?:的)?类型|schema\s*(?:类型|type)", re.I)


def _user_image_preference(
    messages: list[str], *, start: int = 0,
) -> tuple[Literal["generate", "omit", "provided"] | None, PageFactCitation | None]:
    """Read directions in user turns. A missing URL does not cancel a request to generate."""
    preference, citation = None, None
    for index, message in enumerate(messages[start:], start):
        image_context = False
        for clause in _CLAUSE.split(message):
            clean = _LINK.sub("", clause).strip()
            refers_to_image = bool(_IMAGE.search(clean))
            if not refers_to_image and not image_context:
                continue
            image_context = refers_to_image
            generation = _GENERATE.search(clean)
            excluded = _EXCLUDE_IMAGE.search(clean)
            if _NEGATED_GENERATION.search(clean):
                preference, citation = "omit", None
                continue
            links = [match.group().rstrip(".") for match in _LINK.finditer(clause)]
            if len(links) == 1 and _public_image_url(links[0]) and not excluded and (
                refers_to_image or not clean or _USE_LINK.search(clean)
            ):
                preference = "provided"
                citation = PageFactCitation(source_path=f"/user_supplied/{index}", quote=links[0])
            elif generation and not (excluded and excluded.start() > generation.end()) and (
                refers_to_image or re.search(r"一个|一张|新的|\bone\b", clean, re.I)
                or not clean[generation.end():].strip(" 吧就好即可.!?")
            ):
                preference, citation = "generate", None
            elif refers_to_image and (excluded or _NO_URL.search(clean)):
                # A constraint on the original logo/URL can coexist with a new or supplied image.
                if preference not in {"generate", "provided"} or not (
                    _NO_URL.search(clean) or re.search(r"logo|原(?:有|来的)?图|现有图|地址|链接", clean, re.I)
                ):
                    preference, citation = "omit", None
    return preference, citation


def _copy_notes(items: list[str]) -> list[str]:
    return list(dict.fromkeys(
        text.strip() for text in items
        if text.strip() and not _OPTIONAL_FIELD.search(_PAGE_ADDRESS.sub("", text))
        and not _IMAGE.search(text) and not _SCHEMA_TYPE.search(text)
    ))


def _latest_suggestion(run: AgentRun) -> AgentEvent | None:
    return next((
        event for event in reversed(run.events)
        if event.type in {"tool.completed", "tool.failed"}
        and event.payload.get("name") == "seo_page_suggestions"
    ), None)


def share_image_handoff(run: AgentRun) -> dict[str, Any]:
    receipt = _latest_suggestion(run)
    return receipt.payload["result"]["data"] if receipt is not None else {}


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
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not (parsed.username or parsed.password)
    )


def _has_successful_html(page: Any) -> bool:
    return (
        isinstance(page, dict) and isinstance(page.get("status_code"), int)
        and 200 <= page["status_code"] < 300 and page["status_code"] not in {204, 205, 206}
        and _http_url(page.get("final_url"))
    )


def _has_page_body(page: Any) -> bool:
    return _has_successful_html(page) and bool((page.get("content_excerpt") or "").strip())


def _is_image_preview_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        return urlparse(value).path.startswith(_IMAGE_PREVIEW_PATH)
    except ValueError:
        return False


def _public_image_url(value: Any) -> bool:
    return _http_url(value) and not _is_image_preview_url(value)


def _normalize_draft_citations(
    page: dict[str, Any], draft: PageSeoDraft, identity: str
) -> PageSeoDraft:
    def normalize(citation: PageFactCitation) -> PageFactCitation:
        path = citation.source_path
        if re.fullmatch(r"/h[2-6]/(?:0|[1-9]\d*)", path):
            path = f"/headings{path}"
        elif (
            path == "/page_identity_url"
            and isinstance(citation, StructuredFactCitation)
            and citation.json_pointer == "/url"
            and draft.json_ld is not None
            and citation.quote == draft.json_ld.url
        ):
            if citation.quote == page.get("final_url"):
                path = "/final_url"
            elif citation.quote == identity == page.get("canonical_url"):
                path = "/canonical_url"
        if path != citation.source_path:
            return citation.model_copy(update={"source_path": path})
        return citation

    return draft.model_copy(update={
        "citations": [normalize(citation) for citation in draft.citations],
        "json_ld_citations": [normalize(citation) for citation in draft.json_ld_citations],
    })


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

    def turn_runtime_metadata(self, run: AgentRun) -> dict[str, Any]:
        # A new run may contain earlier dialogue. Only the current request and its
        # submitted supplements can authorize another image generation.
        accepted = sum(event.type == "input.submitted" for event in run.events)
        if not accepted and run.human_input and run.human_input.answer:
            accepted = 1
        return {"page_suggestion_user_turns": min(5, 1 + accepted)}

    def planned_continuation(
        self, run: AgentRun, can_call: bool, available_tools: list[str],
    ) -> ToolCall | str | None:
        receipt = _latest_suggestion(run)
        if receipt is None:
            return None
        data = receipt.payload["result"]["data"]
        action = data.get("share_image_action")
        if action is None:
            return None
        text = (
            "已整理 TDK 和分享文案草稿，请在落地前核对。"
            if data.get("proposal") else "本次未取得通过校验的 TDK 草稿，已保留页面证据。"
        )
        if action != "generate":
            return text
        page = data.get("evidence", {}).get("page", {}).get("raw", {}).get("data", {})
        target = page.get("requested_url") or page.get("final_url")
        image = None
        for event in reversed(run.events):
            if event.type not in {"tool.completed", "tool.failed"}:
                continue
            payload = event.payload
            if payload.get("name") != SHARE_IMAGE_TOOL:
                continue
            candidate = payload["result"]["data"]
            candidate_page = (
                candidate.get("evidence", {}).get("page", {}).get("raw", {}).get("data", {})
            )
            if payload["tool_call_id"] == PLANNED_SHARE_IMAGE_ID or (
                target and target in {
                    candidate_page.get("requested_url"), candidate_page.get("final_url"),
                }
            ):
                image = candidate
                break
        if image is None and can_call and SHARE_IMAGE_TOOL in available_tools and target:
            return ToolCall(PLANNED_SHARE_IMAGE_ID, SHARE_IMAGE_TOOL, {"url": target})
        if image is not None:
            if image.get("image_status") == "ready":
                return text + "分享图可在卡片中预览，部署公开图片后再补入 og:image。"
            if image.get("image_status") == "not_configured":
                return text + "生图服务未配置，本次未取得可用的分享图，og:image 暂留空。"
        return text + "本次未取得可用的分享图，og:image 暂留空。"

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
        prior_page = (prior or {}).get("raw", {}).get("data", {})
        prior_url = prior_page.get("requested_url")
        answer = (resume or {}).get("answer") or ""
        use_available = bool(resume and resume.get("action") == "use_available")
        user_messages = list(self.user_messages)
        if answer and not use_available and answer[:2000] not in user_messages:
            user_messages = [*user_messages, answer[:2000]][-5:]
        turn_count = runtime.metadata.get("page_suggestion_user_turns", 5) if runtime else 5
        image_action, image_citation = _user_image_preference(
            user_messages, start=max(0, len(user_messages) - turn_count),
        )
        supplied_image = image_citation.quote if image_citation else None
        same_target = resume and options.url in {resume["tool_arguments"].get("url"), prior_url}
        # A new page address is a source, whereas an image address is not a page correction.
        answer_links = _LINK.findall(answer)
        new_page = (
            answer_links[0].rstrip(".") if len(answer_links) == 1 and not _IMAGE.search(answer)
            and (_PAGE_ADDRESS.search(answer) or answer.strip() == answer_links[0]) else None
        )
        if new_page and not _has_page_body(prior_page):
            same_target = False
            options = options.model_copy(update={"url": new_page})
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
        incomplete = not _has_page_body(page)
        supplied_body = bool(answer and not use_available and any(
            clause.strip() and not _IMAGE.search(clause) and not _GENERATE.search(clause)
            and not _NO_URL.search(clause) and not _EXCLUDE_IMAGE.search(clause)
            and not _LINK.search(clause)
            for clause in _CLAUSE.split(answer)
        ))
        if incomplete:
            steps.coverage["page"]["status"] = "partial"
        if incomplete and not (
            supplied_body and isinstance(page, dict) and _http_url(page.get("final_url"))
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
            if not _has_successful_html(page):
                # Keep the raw response as evidence but do not draft from an error page.
                keys = {"source", "requested_url", "final_url", "status_code"}
                page = {key: value for key, value in page.items() if key in keys}
        page = {**page, "user_supplied": user_messages}
        identity = (
            page.get("canonical_url") if _http_url(page.get("canonical_url")) else page["final_url"]
        )
        for attempt in range(2):
            steps.coverage["draft"] = {"status": "failed"}
            steps.coverage.pop("validation", None)
            try:
                context = PageSuggestionInput(
                    page=page,
                    page_identity_url=identity,
                ).model_dump(mode="json")
                context["share_image_preference"] = image_action or "default"
                if attempt:
                    context["correction"] = (
                        "前次草稿未通过校验。重新从已给的 page 取证，citations 只选 1 至 6 条"
                        "能逐字匹配的引用。JSON-LD 只使用可自动核对的根 URL、名称、描述和语言；"
                        "省略作者、日期、评级等可选属性。不确定页面类型时省略结构化数据。"
                    )
                async with asyncio.timeout(max(0, steps.deadline - time.monotonic())):
                    result = await self.runner.invoke(
                        prompt_key="seo.page.suggestions",
                        method="json_mode",
                        schema=PageSeoDraft,
                        context=context,
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
                    draft = _normalize_draft_citations(page, draft, identity)
                    if image_action or _is_image_preview_url(draft.image_url):
                        draft = draft.model_copy(update={
                            "image_url": supplied_image, "image_citation": image_citation,
                        })
                    document = validate_page_draft(page, draft, identity)
                    if span:
                        span.output_data = {"valid": True, "schema_type": draft.schema_type}
                steps.coverage["validation"]["status"] = "complete"
            except Exception as exc:
                draft_returned = steps.coverage["draft"]["status"] == "complete"
                retryable = isinstance(exc, (OutputParserException, ValidationError)) or (
                    draft_returned and isinstance(exc, ValueError)
                )
                if attempt == 0 and retryable and time.monotonic() < steps.deadline:
                    continue
                if isinstance(exc, (ModelConfigurationError, ModelRequestError)):
                    title = "暂未取得可用建议"
                    reason = f"{exc} "
                elif isinstance(exc, TimeoutError):
                    title = "建议生成超时"
                    reason = "已读到页面信息，生成建议超过本次等待时间。"
                elif draft_returned:
                    title = "建议未通过证据校验"
                    reason = "草稿中的引用或结构化数据与本次页面证据不一致。"
                else:
                    title = "暂未取得可用建议"
                    reason = "本次模型调用没有返回可用的建议。"
                return steps.finish(
                    "page_suggestions",
                    "页面 SEO 信息建议",
                    [{
                        "key": "unavailable",
                        "title": title,
                        "summary": f"{reason}页面证据已保留，可在当前对话重新生成建议。",
                    }],
                    limitations,
                    suggestion_status="needs_review",
                    proposal=None,
                    share_image_action=image_action,
                    user_supplied=user_messages,
                )
            else:
                break
        draft_questions = _copy_notes(draft.questions)
        questions = [] if answer or use_available or image_action == "generate" else draft_questions
        missing = [note for note in _copy_notes(draft.missing_information) if note not in draft_questions]
        # The posted page facts remain intact even when its image is unsuitable for the proposal.
        image_url = draft.image_url or (None if image_action else page.get("og_image"))
        if image_action == "generate":
            missing.append("og:image 暂留空；取得分享图并部署公开图片后再补入。")
        elif image_action == "omit":
            missing.append("已按你的说明省略 og:image。")
        elif not _public_image_url(image_url):
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
        if _public_image_url(image_url):
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
                            {"label": "建议类型", "value": draft.schema_type or "本次省略结构化数据"}
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
                ],
                limitations,
                suggestion_status="awaiting_user_input" if questions else "draft",
                variant="page-seo-suggestions",
                questions=questions,
                share_image_action=image_action,
                proposal={
                    **draft.model_dump(mode="json", by_alias=True),
                    "json_ld": document,
                    "open_graph": og,
                    "html_snippet": snippet,
                    "missing_information": missing,
                    "canonical_observed": page.get("canonical_url"),
                    "requires_human_review": True,
                    "user_supplied": user_messages,
                    "questions": questions,
                },
            )
        )
