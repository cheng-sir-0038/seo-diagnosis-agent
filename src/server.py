"""
FastAPI 服务入口

启动命令::

    uv run uvicorn src.server:app --reload --host 127.0.0.1 --port 8000

API 文档自动生成:
  - Swagger UI: http://localhost:8000/docs
  - ReDoc: http://localhost:8000/redoc

生命周期:
  1. startup:  初始化 CacheManager → 初始化 DataFacade（加载所有 Provider）
  2. running:  处理 API 请求
  3. shutdown: 关闭 Facade（关闭所有 Provider 连接）→ 关闭 CacheManager

架构总览::

    POST /api/v1/query
        │
        ▼
    ┌─────────────────┐
    │  exception_handler │ ← 捕获 InternalXxxError → 消毒 → ErrorResponse
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  query_router     │ ← 参数校验 (QueryRequest schema)
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  DataFacade       │ ← Capability 路由 → Provider 选择 → 缓存 → 包装
    └────────┬─────────┘
             │
    ┌────────▼──────────────┐
    │ DataForSEO / Ahrefs   │ ← Provider 边缘适配
    └────────┬──────────────┘
             │
    ┌────────▼──────────────┐
    │ Client / pseudo mode  │ ← 真实 HTTP 或显式模拟响应
    └───────────────────────┘
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from src.agent.run_manager import run_manager
from src.api.routes.agent import router as agent_router
from src.api.routes.observability import router as observability_router
from src.api.routes.promptops import router as promptops_router
from src.api.routes.query import router as query_router
from src.api.routes.seo_agent_approvals import router as seo_agent_approval_router
from src.api.routes.seo_agent_automation import router as seo_agent_automation_router
from src.api.routes.seo_agent_chat import (
    recover_active_seo_chat_runs,
)
from src.api.routes.seo_agent_chat import (
    router as seo_agent_chat_router,
)
from src.api.routes.seo_agent_monitoring import router as seo_agent_monitoring_router
from src.api.routes.seo_agent_notifications import router as seo_agent_notification_router
from src.api.routes.seo_agent_opportunities import router as seo_agent_opportunity_router
from src.api.routes.seo_agent_publishing import router as seo_agent_publishing_router
from src.api.routes.seo_agent_schedules import router as seo_agent_schedule_router
from src.api.routes.seo_agent_system import router as seo_agent_system_router
from src.api.routes.seo_agent_tasks import router as seo_agent_task_router
from src.api.routes.seo_agent_workbench import router as seo_agent_workbench_router
from src.core.cache import cache_manager
from src.core.config import settings
from src.core.exceptions import ErrorResponse, InternalAdapterError, to_error_response
from src.facade.data_facade import facade
from src.observability.runtime import observability_client
from src.promptops.runtime import prompt_registry
from src.seo_agent.runtime import (
    approval_reminder_coordinator,
    automation_lifecycle,
    checkpoint_manager,
    local_schedule_provider,
    notification_service,
    seo_agent_database,
    seo_prompt_seeder,
)

# ============================================================
# 应用生命周期
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 应用生命周期管理。

    Startup:
      1. 配置日志
      2. 初始化缓存管理器（Redis + SQLite）
      3. 初始化 Facade（加载所有 Provider）

    Shutdown:
      1. 关闭 Facade（关闭所有 Provider 连接）
      2. 关闭缓存管理器
    """
    # ========== Startup ==========
    logger.info(f"🚀 {settings.project_name} 启动中...")
    logger.info(f"   环境: {settings.environment}")
    logger.info(f"   地址: http://{settings.host}:{settings.port}")
    logger.info(f"   文档: http://{settings.host}:{settings.port}/docs")

    # 初始化缓存
    await cache_manager.startup()

    # 初始化通用 Agent 观测内核
    await observability_client.startup()
    await prompt_registry.startup()
    await seo_prompt_seeder.seed()

    # 初始化 Facade（加载所有 Provider）
    await facade.startup()

    # 初始化 SEO Agent 业务库与 LangGraph Checkpointer
    await seo_agent_database.startup()
    await checkpoint_manager.startup()
    await notification_service.seed_event_types()
    recovery = await recover_active_seo_chat_runs()
    logger.info(
        "SEO Chat 恢复完成 | recovered={} | blocked={}",
        recovery["recovered"],
        recovery["blocked"],
    )
    if settings.local_scheduler_enabled:
        await local_schedule_provider.startup()
        local_schedule_provider.add_interval_job(
            job_id="approval-reminders",
            callback=approval_reminder_coordinator.run_once,
            hours=max(1, settings.approval_reminder_interval_hours),
        )
        await automation_lifecycle.startup()

    logger.info(f"✅ {settings.project_name} 启动完成")

    yield  # ← 服务运行期间

    # ========== Shutdown ==========
    logger.info(f"🛑 {settings.project_name} 关闭中...")

    await run_manager.shutdown()
    await automation_lifecycle.shutdown()
    await checkpoint_manager.shutdown()
    await seo_agent_database.shutdown()
    await facade.shutdown()
    await cache_manager.shutdown()
    await observability_client.shutdown()

    logger.info(f"✅ {settings.project_name} 已关闭")


# ============================================================
# 创建 FastAPI 应用
# ============================================================

app = FastAPI(
    title=settings.project_name,
    description="""
    ## SEO 诊断 Agent

    提供 SEO Chat、只读工具调用、审批工作流，以及多数据源的统一查询接口。

    ### 核心特性
    - **SEO Chat**: Agent 自主选择只读工具并通过 SSE 返回运行过程
    - **统一查询**: `POST /api/v1/query` 路由到可用的数据源
    - **统一数据模型**: 按能力整理不同数据来源的结果
    - **缓存 + 快照**: 进程内 TTL 缓存和 SQLite 快照已实现，Redis 未接入
    - **标准错误响应**: 对内异常消毒后再对外暴露

    ### 当前注册的数据源
    - **页面 HTML、Serper、DataForSEO、PageSpeed**: 部分需要配置凭证
    - **Ahrefs**: 当前仍为伪模式，不进入正式 SEO Chat 诊断

    ### 后续扩展
    完整能力与限制见仓库 README。
    """,
    version="1.0.0-alpha",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

LOCAL_ORIGIN_PATTERN = r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?"

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN_PATTERN,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reject_external_browser_origins(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and not re.fullmatch(LOCAL_ORIGIN_PATTERN, origin):
        return JSONResponse(status_code=403, content={"detail": "只允许本机前端访问"})
    return await call_next(request)


# ============================================================
# 中间件：请求追踪 ID
# ============================================================


@app.middleware("http")
async def add_trace_id(request: Request, call_next):
    """为每个请求添加 trace_id，用于错误追踪和日志关联。"""
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4())[:8])
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


# ============================================================
# 全局异常处理器
# ============================================================


@app.exception_handler(InternalAdapterError)
async def internal_error_handler(request: Request, exc: InternalAdapterError) -> JSONResponse:
    """对内异常 → 消毒 → 对外 ErrorResponse。

    这是对内/对外隔离的关键转换点：
      - debug_context 等敏感信息只写入日志，绝不进入响应体
      - 对外只暴露 code + message + trace_id
      - 日志中保留完整 debug_context 用于排查
    """
    trace_id = getattr(request.state, "trace_id", None)
    error_response = to_error_response(exc, trace_id=trace_id)

    # 日志记录完整上下文（含敏感信息，用于内部排查）
    logger.error(
        f"Internal Error | trace_id={trace_id} | "
        f"code={exc.error_code.value} | "
        f"provider={exc.provider} | "
        f"debug_context={exc.debug_context}"
    )

    # 响应体只含消毒后的信息
    return JSONResponse(
        status_code=_get_http_status(exc.error_code.value),
        content=error_response.model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：处理所有未被显式捕获的异常。

    不泄露任何内部细节，统一返回 INTERNAL_ERROR。
    """
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.exception(f"未预期错误 | trace_id={trace_id} | {exc}")

    error_response = ErrorResponse(
        code="INTERNAL_ERROR",
        message="服务内部错误，请联系管理员",
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=500,
        content=error_response.model_dump(mode="json"),
    )


def _get_http_status(error_code: str) -> int:
    """ErrorCode → HTTP 状态码映射。"""
    mapping = {
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
    return mapping.get(error_code, 500)


# ============================================================
# 注册路由
# ============================================================

app.include_router(query_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(observability_router, prefix="/api/v1")
app.include_router(promptops_router, prefix="/api/v1")
app.include_router(seo_agent_system_router, prefix="/api/v1")
app.include_router(seo_agent_approval_router, prefix="/api/v1")
app.include_router(seo_agent_notification_router, prefix="/api/v1")
app.include_router(seo_agent_schedule_router, prefix="/api/v1")
app.include_router(seo_agent_task_router, prefix="/api/v1")
app.include_router(seo_agent_publishing_router, prefix="/api/v1")
app.include_router(seo_agent_monitoring_router, prefix="/api/v1")
app.include_router(seo_agent_opportunity_router, prefix="/api/v1")
app.include_router(seo_agent_workbench_router, prefix="/api/v1")
app.include_router(seo_agent_automation_router, prefix="/api/v1")
app.include_router(seo_agent_chat_router, prefix="/api/v1")


# ============================================================
# 健康检查
# ============================================================


@app.get("/health", tags=["System"])
async def health_check():
    """服务健康检查。

    返回各 Provider 的连接状态和当前额度信息。

    Returns:
        {"status": "ok|degraded", "providers": {"dataforseo": {...}, "ahrefs": {...}}}
    """
    provider_statuses = {}
    for name, provider in facade._providers.items():
        try:
            status = await provider.health_check()
            provider_statuses[name] = status
        except Exception as e:
            provider_statuses[name] = {"status": "error", "message": str(e)}

    all_ok = all(s.get("status") == "ok" for s in provider_statuses.values())

    return {
        "status": "ok" if all_ok else "degraded",
        "version": "1.0.0-pseudo",
        "providers": provider_statuses,
    }


# ============================================================
# 直接启动（开发用）
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
