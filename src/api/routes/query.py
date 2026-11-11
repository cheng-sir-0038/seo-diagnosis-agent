"""
API 路由 — POST /api/v1/query

统一数据查询入口。
所有请求必须符合 ``QueryRequest`` schema，返回标准化的 ``QueryResponse``。
调用方不关心数据来自哪个 Provider——Facade 自动路由。

请求格式::

    POST /api/v1/query
    {
        "capability": "search_metrics",
        "site_id": "example.com",
        "params": {
            "country": "us"
        },
        "force_refresh": false,
        "task_id": "t_001"
    }

响应格式::

    {
        "meta": {
            "capability": "search_metrics",
            "source": "ahrefs",
            "params_hash": "a1b2c3d4",
            "cached": false
        },
        "data": {
            "domain_rating": 68.5,
            "organic_traffic_estimate": 125000,
            ...
        }
    }

错误响应格式::

    {
        "code": "AUTH_FAILED",
        "message": "数据源授权失败，请检查配置",
        "details": [],
        "trace_id": "req_abc123"
    }
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.core.exceptions import ErrorResponse, InternalAdapterError, to_error_response
from src.facade.data_facade import facade
from src.models.common import Capability

router = APIRouter(tags=["Data Query"])


# ============================================================
# 请求 Schema
# ============================================================

class QueryRequest(BaseModel):
    """统一查询请求 Schema。

    调用方只需要传 capability + site_id + params，
    不关心数据来自哪个 Provider 或哪个 API 端点。

    Attributes:
        capability: 业务能力标识（枚举，不是自由字符串）。
        site_id: 站点 ID 或域名。部分 capability（如 subscription_info）不需要。
        params: 附加查询参数（country、limit、keywords、date_range 等）。
        force_refresh: 是否强制跳过缓存。
        task_id: 可选的任务 ID，用于证据快照关联。
    """

    capability: Capability = Field(
        ...,
        description="业务能力标识，如 search_metrics / audit_health / backlinks_list",
        examples=["search_metrics"],
    )
    site_id: str | None = Field(
        default=None,
        description="站点 ID 或域名。subscription_info 可不传",
        examples=["example.com"],
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="附加查询参数（country, limit, keywords, date_range 等）",
        examples=[{"country": "us", "limit": 10}],
    )
    force_refresh: bool = Field(
        default=False,
        description="是否跳过缓存强制刷新",
    )
    task_id: str | None = Field(
        default=None,
        description="关联的任务 ID，用于证据快照",
    )


# ============================================================
# 路由
# ============================================================

@router.post(
    "/query",
    summary="统一数据查询入口",
    description="""
    所有数据查询的唯一入口。

    **支持的 capability 枚举值**（Capability enum）:

    | 分组 | capability | 说明 |
    |------|-----------|------|
    | 管理 | `site_profile` | 站点档案 |
    | 管理 | `subscription_info` | 额度信息 |
    | 搜索 | `search_metrics` | 站点搜索表现总览 |
    | 搜索 | `top_pages` | Top Pages 榜单 |
    | 搜索 | `organic_keywords` | 自然关键词 |
    | 搜索 | `search_history` | 搜索历史趋势 |
    | 审计 | `audit_health` | 站点健康分 |
    | 审计 | `audit_issues` | 技术问题列表 |
    | 审计 | `audit_page_explorer` | Page Explorer |
    | 关键词 | `keyword_overview` | 关键词概览 |
    | 关键词 | `serp_overview` | SERP 结果 |
    | 关键词 | `autocomplete` | Google 搜索联想词 |
    | 内容 | `news_search` | Google 新闻结果 |
    | 内容 | `image_search` | Google 图片结果 |
    | 内容 | `video_search` | Google 视频结果 |
    | 排名 | `rank_overview` | 排名总览 |
    | 排名 | `rank_detail` | 关键词排名详情 |
    | 外链 | `backlinks_list` | 外链列表 |
    | 外链 | `refdomains` | 引用域名 |
    | 外链 | `broken_backlinks` | 失效外链 |
    | 竞争 | `organic_competitors` | 自然搜索竞争对手 |
    | 竞争 | `batch_analysis` | 批量域名分析 |
    """,
    responses={
        200: {"description": "查询成功"},
        400: {"description": "参数错误（INVALID_PARAMS / UNSUPPORTED_CAPABILITY）"},
        401: {"description": "授权失败（AUTH_FAILED）"},
        429: {"description": "速率限制（RATE_LIMITED）"},
        502: {"description": "上游服务错误（UPSTREAM_ERROR）"},
        503: {"description": "数据不可用（DATA_NOT_AVAILABLE）"},
    },
)
async def query(request: Request, body: QueryRequest) -> dict[str, Any]:
    """统一数据查询。

    由 Facade 自动路由到正确的 Provider → 对应业务方法。
    如果命中缓存则直接返回，不消耗 API Units。

    Args:
        request: FastAPI Request 对象（用于提取 trace_id 等元信息）。
        body: 查询请求体。

    Returns:
        标准化的 {"meta": ..., "data": ...} 响应。

    Raises:
        HTTPException: 各种错误通过 exception_handler 转换为标准 ErrorResponse。
    """
    logger.info(
        f"API Query | capability={body.capability.value} | "
        f"site_id={body.site_id} | params={body.params}"
    )

    try:
        result = await facade.query(
            capability=body.capability,
            site_id=body.site_id,
            params=body.params,
            force_refresh=body.force_refresh,
            task_id=body.task_id,
        )
        return result

    except InternalAdapterError as e:
        # 对内异常 → 对外 ErrorResponse → HTTPException
        trace_id = getattr(request.state, "trace_id", None)
        error_response = to_error_response(e, trace_id=trace_id)
        logger.error(
            f"查询失败 | code={error_response.code.value} | "
            f"message={error_response.message} | "
            f"debug={e.debug_context}"
        )
        # 对外暴露消毒后的 ErrorResponse
        raise _to_http_exception(error_response)


# ============================================================
# HTTP 异常转换
# ============================================================

_ERROR_CODE_TO_HTTP: dict = {
    "INVALID_PARAMS": 400,
    "UNSUPPORTED_CAPABILITY": 400,
    "SITE_NOT_FOUND": 404,
    "AUTH_FAILED": 401,
    "RATE_LIMITED": 429,
    "QUOTA_EXHAUSTED": 429,
    "UPSTREAM_ERROR": 502,
    "UPSTREAM_TIMEOUT": 504,
    "DATA_NOT_AVAILABLE": 503,
    "INTERNAL_ERROR": 500,
}


def _to_http_exception(error: Any) -> HTTPException:
    """将 ErrorResponse 转换为 FastAPI HTTPException。

    对外只泄露 ErrorResponse 的标准化字段。
    """
    if isinstance(error, ErrorResponse):
        status_code = _ERROR_CODE_TO_HTTP.get(error.code.value, 500)
        return HTTPException(
            status_code=status_code,
            detail=error.model_dump(mode="json"),
        )
    return HTTPException(status_code=500, detail=str(error))
