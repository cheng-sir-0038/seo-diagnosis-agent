from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from src.agent.tools.base import AgentTool, RenderHint, ToolResult, ToolRuntimeContext
from src.agent.tools.registry import ToolRegistry
from src.core.config import settings
from src.models.common import Capability
from src.seo_agent.chat.presentation import build_presentation, can_render
from src.seo_agent.workflows.site_audit import SiteAuditWorkflow


class FacadeProtocol(Protocol):
    def live_capabilities(self) -> set[Capability]: ...

    async def query_live(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SeoToolDefinition:
    name: str
    description: str
    capability: Capability
    parameters: dict[str, Any]
    render_hint: RenderHint | None = None


_COMMON_PROPERTIES = {
    "country": {
        "type": "string",
        "description": "ISO 3166-1 两位国家代码。未提供时使用站点默认国家。",
    },
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
    "force_refresh": {"type": "boolean", "default": False},
}


def _parameters(
    *, required: list[str] | None = None, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    properties = dict(_COMMON_PROPERTIES)
    properties.update(extra or {})
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


SEO_TOOL_DEFINITIONS = (
    SeoToolDefinition(
        "seo_search_metrics",
        "读取当前站点的自然搜索总览指标。适合先判断流量、关键词数量和排名覆盖是否异常。",
        Capability.SEARCH_METRICS,
        _parameters(),
        "chart",
    ),
    SeoToolDefinition(
        "seo_search_history",
        "读取当前站点的自然搜索历史趋势，用于判断变化发生的时间和方向。",
        Capability.SEARCH_HISTORY,
        _parameters(),
        "chart",
    ),
    SeoToolDefinition(
        "seo_keyword_overview",
        "批量查询关键词搜索量、难度、点击和商业价值等指标。",
        Capability.KEYWORD_OVERVIEW,
        _parameters(
            required=["keywords"],
            extra={
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 50,
                }
            },
        ),
        "table",
    ),
    SeoToolDefinition(
        "seo_autocomplete",
        (
            "查询 Google 搜索联想词，用于发现长尾词、问题词和用户真实表达。"
            "本工具不提供搜索量、难度、CPC 或排名。"
        ),
        Capability.AUTOCOMPLETE,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "country": {
                    "type": "string",
                    "description": "ISO 3166-1 两位国家代码。",
                },
                "language": {
                    "type": "string",
                    "description": "语言代码；未提供时使用站点默认语言。",
                },
                "force_refresh": {"type": "boolean", "default": False},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "table",
    ),
    SeoToolDefinition(
        "seo_news_search",
        "查询近期 Google 新闻结果，用于内容时效性和媒体关注分析。",
        Capability.NEWS_SEARCH,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "country": {"type": "string"},
                "language": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "page": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
                "autocorrect": {"type": "boolean", "default": True},
                "force_refresh": {"type": "boolean", "default": False},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "cards",
    ),
    SeoToolDefinition(
        "seo_image_search",
        "查询 Google 图片结果，用于分析图片搜索竞争、来源页面和图片尺寸。",
        Capability.IMAGE_SEARCH,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "country": {"type": "string"},
                "language": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "page": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
                "autocorrect": {"type": "boolean", "default": True},
                "force_refresh": {"type": "boolean", "default": False},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "cards",
    ),
    SeoToolDefinition(
        "seo_video_search",
        "查询 Google 视频结果，用于分析视频竞争、平台、频道、时长和发布时间。",
        Capability.VIDEO_SEARCH,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "country": {"type": "string"},
                "language": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "page": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
                "autocorrect": {"type": "boolean", "default": True},
                "force_refresh": {"type": "boolean", "default": False},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "cards",
    ),
    SeoToolDefinition(
        "seo_serp_overview",
        "查询一个关键词的实时 Google SERP，用于分析排名页面、竞争强度和搜索意图。",
        Capability.SERP_OVERVIEW,
        _parameters(
            required=["keyword"],
            extra={
                "keyword": {"type": "string", "minLength": 1},
                "language": {
                    "type": "string",
                    "description": "两位语言代码；未提供时使用站点默认语言。",
                },
                "page": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
                "autocorrect": {"type": "boolean", "default": True},
                "purpose": {
                    "type": "string",
                    "enum": [
                        "quick_lookup",
                        "seo_analysis",
                        "rank_monitoring",
                        "local_analysis",
                        "research",
                    ],
                    "default": "quick_lookup",
                    "description": "查询目的；影响系统选择 Serper 或 DataForSEO。",
                },
                "preferred_provider": {
                    "type": "string",
                    "enum": ["serper", "dataforseo"],
                    "description": "仅在用户明确指定数据源时填写。",
                },
                "allow_fallback": {
                    "type": "boolean",
                    "default": True,
                    "description": "首选数据源失败时，是否允许切换到完整满足要求的备用数据源。",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "device": {"type": "string", "enum": ["desktop", "mobile"]},
                "os": {
                    "type": "string",
                    "enum": ["windows", "macos", "android", "ios"],
                },
                "location_name": {
                    "type": "string",
                    "description": "DataForSEO 完整位置名，例如 London,England,United Kingdom。",
                },
                "location_coordinate": {
                    "type": "string",
                    "description": "纬度,经度,半径，例如 51.5074,-0.1278,200。",
                },
                "load_ai_overview": {
                    "type": "boolean",
                    "default": False,
                    "description": "仅在用户明确要求 AI Overview 时开启；可能产生额外费用。",
                },
                "result_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                    "description": "仅返回指定 SERP 元素，例如 organic、ai_overview。",
                },
                "se_domain": {
                    "type": "string",
                    "pattern": r"^google\.[a-z0-9.-]+$",
                    "description": "可选 Google 域名，例如 google.co.uk。",
                },
            },
        ),
        "table",
    ),
    SeoToolDefinition(
        "seo_audit_issues",
        "读取站点技术审计问题，可按严重级别过滤。",
        Capability.AUDIT_ISSUES,
        _parameters(extra={"severity": {"type": "string", "description": "可选严重级别"}}),
        "tree",
    ),
    SeoToolDefinition(
        "seo_backlinks_list",
        "读取站点外链列表，用于分析来源域、锚文本和链接质量。",
        Capability.BACKLINKS_LIST,
        _parameters(),
        "table",
    ),
    SeoToolDefinition(
        "seo_organic_competitors",
        "读取自然搜索竞争对手及关键词交集，用于判断竞争变化。",
        Capability.ORGANIC_COMPETITORS,
        _parameters(),
        "table",
    ),
    SeoToolDefinition(
        "seo_site_profile",
        "读取站点画像和数据源项目绑定信息。",
        Capability.SITE_PROFILE,
        _parameters(),
        "tree",
    ),
    SeoToolDefinition(
        "seo_organic_keywords",
        "读取站点当前自然关键词及排名、流量等字段。",
        Capability.ORGANIC_KEYWORDS,
        _parameters(),
        "table",
    ),
    SeoToolDefinition(
        "seo_top_pages",
        "读取站点表现最好的自然搜索页面。",
        Capability.TOP_PAGES,
        _parameters(),
        "table",
    ),
    SeoToolDefinition(
        "seo_page_metadata",
        (
            "直接读取当前站点页面源 HTML 中的 Title、Meta Description、Meta Keywords、"
            "Canonical、Robots、H1 和语言等元信息。"
            "用户询问当前页面 TDK 或标签内容时必须使用本工具；"
            "SERP 结果和 PageSpeed 不能替代本工具。"
        ),
        Capability.PAGE_METADATA,
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "当前站点内要检查的完整页面 URL；未提供时检查 HTTPS 首页。",
                },
                "force_refresh": {"type": "boolean", "default": False},
            },
            "required": [],
            "additionalProperties": False,
        },
        "chart",
    ),
    SeoToolDefinition(
        "seo_page_performance",
        (
            "检测当前站点内一个具体页面的 PageSpeed/Lighthouse 实验室表现。"
            "默认检测首页移动端，并返回性能、无障碍、最佳实践和 SEO 四类结果；"
            "移动端与桌面端需要分别调用。"
        ),
        Capability.PAGE_PERFORMANCE,
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "当前站点内要检测的完整页面 URL；未提供时检测 HTTPS 首页。",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["mobile", "desktop"],
                    "default": "mobile",
                },
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "performance",
                            "accessibility",
                            "best-practices",
                            "seo",
                        ],
                    },
                    "minItems": 1,
                    "maxItems": 4,
                    "default": [
                        "performance",
                        "accessibility",
                        "best-practices",
                        "seo",
                    ],
                },
                "locale": {"type": "string", "default": "zh-CN"},
                "force_refresh": {"type": "boolean", "default": False},
            },
            "required": [],
            "additionalProperties": False,
        },
        "chart",
    ),
)


class SeoFacadeTool(AgentTool):
    def __init__(
        self, facade: FacadeProtocol, site: dict[str, Any], definition: SeoToolDefinition
    ) -> None:
        self.facade = facade
        self.site = dict(site)
        self.definition = definition
        self.name = definition.name
        self.description = definition.description
        self.parameters_schema = definition.parameters
        self.render_hint = definition.render_hint

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        params = self._params(arguments)
        response = await self.facade.query_live(
            capability=self.definition.capability,
            site_id=str(self.site["domain"]),
            params=params,
            force_refresh=bool(arguments.get("force_refresh", False)),
            task_id=runtime.task_id if runtime else None,
        )
        presentation = build_presentation(self.definition.capability, self.render_hint, response)
        return ToolResult(success=True, data=presentation)

    def resolve_render_hint(self, result_payload: dict[str, Any]) -> RenderHint | None:
        data = result_payload.get("data")
        return (
            self.render_hint
            if isinstance(data, dict) and can_render(self.render_hint, data)
            else None
        )

    def _params(self, arguments: dict[str, Any]) -> dict[str, Any]:
        properties = self.parameters_schema.get("properties") or {}
        params = {
            key: value
            for key, value in arguments.items()
            if key in properties and key not in {"force_refresh"} and value is not None
        }
        if self.definition.capability == Capability.PAGE_METADATA:
            return self._page_metadata_params(params)
        if self.definition.capability == Capability.PAGE_PERFORMANCE:
            return self._pagespeed_params(params)
        if self.definition.capability in {
            Capability.AUTOCOMPLETE,
            Capability.NEWS_SEARCH,
            Capability.IMAGE_SEARCH,
        }:
            params.setdefault("country", str(self.site.get("default_country") or "US").lower())
            params.setdefault("language", str(self.site.get("default_language") or "en"))

        params.setdefault("country", str(self.site.get("default_country") or "US").lower())
        if "limit" in params:
            maximum = 200 if self.definition.capability == Capability.SERP_OVERVIEW else 100
            params["limit"] = max(1, min(int(params["limit"]), maximum))
        if self.definition.capability == Capability.SERP_OVERVIEW:
            params.setdefault("language", str(self.site.get("default_language") or "en"))
            params.setdefault("page", 1)
            params.setdefault("autocorrect", True)
            params.setdefault("purpose", "quick_lookup")
            params.setdefault("allow_fallback", True)
        for required in self.parameters_schema.get("required") or []:
            value = params.get(required)
            if value is None or value == "" or value == []:
                raise ValueError(f"缺少工具参数: {required}")
        return params

    def _page_metadata_params(self, params: dict[str, Any]) -> dict[str, Any]:
        site_domain = _hostname(str(self.site.get("domain") or ""))
        if not site_domain:
            raise ValueError("当前站点域名无效，无法检查页面元信息")
        target_url = _normalize_target_url(str(params.get("url") or f"https://{site_domain}/"))
        if _hostname(target_url) != site_domain:
            raise ValueError("页面元信息只允许检查当前站点域名下的页面")
        return {"url": target_url}

    def _pagespeed_params(self, params: dict[str, Any]) -> dict[str, Any]:
        site_domain = _hostname(str(self.site.get("domain") or ""))
        if not site_domain:
            raise ValueError("当前站点域名无效，无法执行 PageSpeed 检测")
        target_url = _normalize_target_url(str(params.get("url") or f"https://{site_domain}/"))
        if _hostname(target_url) != site_domain:
            raise ValueError("PageSpeed 只允许检测当前站点域名下的页面")

        strategy = str(params.get("strategy") or "mobile").lower()
        if strategy not in {"mobile", "desktop"}:
            raise ValueError("PageSpeed strategy 仅支持 mobile 或 desktop")
        categories: list[str] = []
        default_categories = ["performance", "accessibility", "best-practices", "seo"]
        for value in params.get("categories") or default_categories:
            item = str(value).lower().replace("_", "-")
            if item not in set(default_categories):
                raise ValueError(f"不支持的 PageSpeed category: {value}")
            if item not in categories:
                categories.append(item)
        return {
            "url": target_url,
            "strategy": strategy,
            "categories": categories,
            "locale": str(params.get("locale") or "zh-CN"),
        }


def build_seo_agent_registry(facade: FacadeProtocol, site: dict[str, Any]) -> ToolRegistry:
    live_capabilities = facade.live_capabilities()
    registry = ToolRegistry(
        [
            SeoFacadeTool(facade, site, definition)
            for definition in SEO_TOOL_DEFINITIONS
            if definition.capability in live_capabilities
        ]
    )
    if Capability.PAGE_METADATA in live_capabilities:
        performance = (
            registry.get("seo_page_performance")
            if Capability.PAGE_PERFORMANCE in live_capabilities else None
        )
        registry.register(SiteAuditWorkflow(
            registry.get("seo_page_metadata"), performance, settings.agent_tool_timeout
        ))
    return registry


def _hostname(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    hostname = urlparse(text).hostname or ""
    return hostname.lower().removeprefix("www.")


def _normalize_target_url(value: str) -> str:
    text = value.strip()
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("PageSpeed url 必须是有效的 http 或 https URL")
    return parsed._replace(path=parsed.path or "/", fragment="").geturl()
