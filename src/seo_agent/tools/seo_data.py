from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from src.agent.tools import (
    AgentTool,
    RenderHint,
    ToolResult,
    ToolRuntimeContext,
    to_langchain_tool,
)
from src.models.common import Capability
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind, Status
from src.routing.contracts import QueryPurpose


class FacadeProtocol(Protocol):
    async def query(self, **kwargs: Any) -> dict[str, Any]: ...


class SeoToolInput(BaseModel):
    site_id: str
    country: str = "us"
    limit: int = Field(default=20, ge=1, le=100)
    task_id: str | None = None
    force_refresh: bool = False


class KeywordToolInput(SeoToolInput):
    limit: int = Field(default=20, ge=1, le=200)
    keyword: str
    language: str = Field(default="en", min_length=2, max_length=16)
    page: int = Field(default=1, ge=1, le=10)
    autocorrect: bool = True
    purpose: QueryPurpose = QueryPurpose.QUICK_LOOKUP
    preferred_provider: str | None = Field(default=None, pattern="^(serper|dataforseo)$")
    allow_fallback: bool = True
    device: str | None = Field(default=None, pattern="^(desktop|mobile)$")
    os: str | None = Field(default=None, pattern="^(windows|macos|android|ios)$")
    location_name: str | None = None
    location_coordinate: str | None = None
    load_ai_overview: bool = False
    result_types: list[str] | None = None
    se_domain: str | None = None


class AutocompleteToolInput(BaseModel):
    site_id: str
    query: str = Field(min_length=1)
    country: str = "us"
    language: str = "en"
    task_id: str | None = None
    force_refresh: bool = False


class NewsToolInput(AutocompleteToolInput):
    limit: int = Field(default=10, ge=1, le=100)
    page: int = Field(default=1, ge=1, le=10)
    autocorrect: bool = True


class ImageSearchToolInput(NewsToolInput):
    pass


class VideoSearchToolInput(NewsToolInput):
    pass


@dataclass(frozen=True)
class SeoDataToolDefinition:
    name: str
    description: str
    capability: Capability
    parameters_schema: dict[str, Any]
    render_hint: RenderHint | None = None


SEO_DATA_TOOL_DEFINITIONS = (
    SeoDataToolDefinition(
        name="seo_organic_keywords",
        description="读取站点自然关键词，返回统一证据数据。",
        capability=Capability.ORGANIC_KEYWORDS,
        parameters_schema=SeoToolInput.model_json_schema(),
        render_hint="table",
    ),
    SeoDataToolDefinition(
        name="seo_serp_overview",
        description="读取指定关键词的 SERP 结果。",
        capability=Capability.SERP_OVERVIEW,
        parameters_schema=KeywordToolInput.model_json_schema(),
        render_hint="table",
    ),
    SeoDataToolDefinition(
        name="seo_autocomplete",
        description="读取 Google 搜索联想词，不包含搜索量、难度、CPC 或排名。",
        capability=Capability.AUTOCOMPLETE,
        parameters_schema=AutocompleteToolInput.model_json_schema(),
        render_hint="table",
    ),
    SeoDataToolDefinition(
        name="seo_news_search",
        description="读取近期 Google 新闻结果，用于内容时效性分析。",
        capability=Capability.NEWS_SEARCH,
        parameters_schema=NewsToolInput.model_json_schema(),
        render_hint="cards",
    ),
    SeoDataToolDefinition(
        name="seo_image_search",
        description="读取 Google 图片结果和来源页面。",
        capability=Capability.IMAGE_SEARCH,
        parameters_schema=ImageSearchToolInput.model_json_schema(),
        render_hint="cards",
    ),
    SeoDataToolDefinition(
        name="seo_video_search",
        description="读取 Google 视频搜索结果。",
        capability=Capability.VIDEO_SEARCH,
        parameters_schema=VideoSearchToolInput.model_json_schema(),
        render_hint="cards",
    ),
    SeoDataToolDefinition(
        name="seo_top_pages",
        description="读取站点表现最好的页面。",
        capability=Capability.TOP_PAGES,
        parameters_schema=SeoToolInput.model_json_schema(),
        render_hint="table",
    ),
    SeoDataToolDefinition(
        name="seo_organic_competitors",
        description="读取自然搜索竞争对手。",
        capability=Capability.ORGANIC_COMPETITORS,
        parameters_schema=SeoToolInput.model_json_schema(),
        render_hint="table",
    ),
)


class SeoDataProtocolTool(AgentTool):
    def __init__(
        self,
        bridge: SeoDataToolBridge,
        definition: SeoDataToolDefinition,
    ) -> None:
        self.bridge = bridge
        self.definition = definition
        self.name = definition.name
        self.description = definition.description
        self.parameters_schema = definition.parameters_schema
        self.render_hint = definition.render_hint

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        try:
            if self.definition.capability in {
                Capability.AUTOCOMPLETE,
                Capability.NEWS_SEARCH,
                Capability.IMAGE_SEARCH,
            }:
                query = str(arguments.get("query") or "").strip()
                if not query:
                    return ToolResult(success=False, error="query 不能为空")
                params = {
                    "query": query,
                    "country": str(arguments.get("country") or "us"),
                    "language": str(arguments.get("language") or "en"),
                }
                if self.definition.capability in {
                    Capability.NEWS_SEARCH,
                    Capability.IMAGE_SEARCH,
                    Capability.VIDEO_SEARCH,
                }:
                    params.update(
                        {
                            "limit": max(1, min(int(arguments.get("limit") or 10), 100)),
                            "page": max(1, min(int(arguments.get("page") or 1), 10)),
                            "autocorrect": bool(arguments.get("autocorrect", True)),
                        }
                    )
            else:
                maximum_limit = (
                    200 if self.definition.capability == Capability.SERP_OVERVIEW else 100
                )
                params = {
                    "country": str(arguments.get("country") or "us"),
                    "limit": max(
                        1,
                        min(int(arguments.get("limit") or 20), maximum_limit),
                    ),
                }
            if self.definition.capability == Capability.SERP_OVERVIEW:
                keyword = str(arguments.get("keyword") or "").strip()
                if not keyword:
                    return ToolResult(success=False, error="keyword 不能为空")
                params.update(
                    {
                        "keyword": keyword,
                        "language": str(arguments.get("language") or "en"),
                        "page": max(1, min(int(arguments.get("page") or 1), 10)),
                        "autocorrect": bool(arguments.get("autocorrect", True)),
                        "purpose": str(arguments.get("purpose") or QueryPurpose.QUICK_LOOKUP.value),
                        "preferred_provider": (
                            str(arguments["preferred_provider"])
                            if arguments.get("preferred_provider")
                            else None
                        ),
                        "allow_fallback": bool(arguments.get("allow_fallback", True)),
                        "device": arguments.get("device"),
                        "os": arguments.get("os"),
                        "location_name": arguments.get("location_name"),
                        "location_coordinate": arguments.get("location_coordinate"),
                        "load_ai_overview": bool(arguments.get("load_ai_overview", False)),
                        "result_types": arguments.get("result_types"),
                        "se_domain": arguments.get("se_domain"),
                    }
                )
                params = {key: value for key, value in params.items() if value is not None}
            result = await self.bridge.query(
                capability=self.definition.capability,
                site_id=str(arguments.get("site_id") or "").strip(),
                params=params,
                task_id=(
                    str(arguments["task_id"])
                    if arguments.get("task_id")
                    else runtime.task_id
                    if runtime
                    else None
                ),
                force_refresh=bool(arguments.get("force_refresh", False)),
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data=result)


class SeoDataToolBridge:
    def __init__(
        self,
        facade: FacadeProtocol,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.facade = facade
        self.observability = observability

    async def query(
        self,
        *,
        capability: Capability,
        site_id: str,
        params: dict[str, Any] | None = None,
        task_id: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        arguments = {
            "capability": capability.value,
            "site_id": site_id,
            "params": dict(params or {}),
            "task_id": task_id,
            "force_refresh": force_refresh,
        }
        started = time.perf_counter()
        async with observed_operation(
            self.observability,
            f"seo_data.{capability.value}",
            kind=SpanKind.ADAPTER,
            component_name="data_facade",
            input_data=arguments,
            trigger_type="tool",
        ) as span:
            try:
                result = await self.facade.query(
                    capability=capability,
                    site_id=site_id,
                    params=dict(params or {}),
                    task_id=task_id,
                    force_refresh=force_refresh,
                )
            except Exception as exc:
                if span is not None:
                    await span.tool_call(
                        tool_call_id=f"seo-{uuid.uuid4().hex}",
                        tool_name=f"seo_data.{capability.value}",
                        adapter_key="data_facade",
                        operation="query",
                        arguments=arguments,
                        status=Status.FAILED,
                        latency_ms=round((time.perf_counter() - started) * 1000, 3),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                    await span.capture_exception(
                        exc,
                        issue_type="SEO_DATA_QUERY_FAILED",
                        component=f"data_facade.{capability.value}",
                    )
                raise
            if span is not None:
                meta = result.get("meta") or {}
                await span.tool_call(
                    tool_call_id=f"seo-{uuid.uuid4().hex}",
                    tool_name=f"seo_data.{capability.value}",
                    adapter_key=str(meta.get("source") or "data_facade"),
                    operation="query",
                    arguments=arguments,
                    result_summary={
                        "source": meta.get("source"),
                        "cached": meta.get("cached"),
                        "data_type": type(result.get("data")).__name__,
                    },
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
            return result

    def build_protocol_tools(self) -> list[SeoDataProtocolTool]:
        return [SeoDataProtocolTool(self, definition) for definition in SEO_DATA_TOOL_DEFINITIONS]

    def build_tools(self) -> list[Any]:
        """Compatibility outlet for existing LangChain/LangGraph callers."""
        return [to_langchain_tool(tool) for tool in self.build_protocol_tools()]
