"""
异常体系 — 对内 vs 对外严格分离

对内异常（Provider 内部使用）:
  携带原始响应、堆栈、调试上下文，帮助开发者定位问题。
  命名: InternalXxxError，不会直接暴露给 API 调用方。

对外异常（API 层返回给调用方）:
  经过消毒的标准化错误响应，不含内部实现细节。
  命名: ErrorCode 枚举 + ErrorResponse Pydantic 模型。

转换链路:
  InternalXxxError → exception_handler (server.py) → ErrorResponse → HTTP Response
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel

# ============================================================
# 对外：错误码枚举
# ============================================================

class ErrorCode(enum.StrEnum):
    """对外错误码 — API 调用方依据此码做程序化处理。

    每个错误码对应一个标准化的 HTTP 状态码和人类可读的消息模板。
    调用方不需要解析 message 文本，用 code 做分支判断即可。
    """

    # ---- 客户端错误 (4xx) ----
    INVALID_PARAMS = "INVALID_PARAMS"               # 请求参数校验失败
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"  # 不支持的能力
    SITE_NOT_FOUND = "SITE_NOT_FOUND"               # 站点不存在或未绑定项目
    AUTH_FAILED = "AUTH_FAILED"                     # API Key 无效或过期
    RATE_LIMITED = "RATE_LIMITED"                   # 请求频率过高
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"             # 额度已用完

    # ---- 服务端错误 (5xx) ----
    UPSTREAM_ERROR = "UPSTREAM_ERROR"               # 上游 API 返回错误
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"           # 上游 API 超时
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"       # 所请求数据暂不可用
    INTERNAL_ERROR = "INTERNAL_ERROR"               # 内部未预期错误


# ============================================================
# 对外：错误响应模型
# ============================================================

class ErrorDetail(BaseModel):
    """单个字段或子错误的详情。

    Attributes:
        field: 出错的字段名或数据域（可选）。
        reason: 错误原因（人类可读）。
    """
    field: str | None = None
    reason: str


class ErrorResponse(BaseModel):
    """对外 API 错误响应的标准结构。

    所有 API 错误统一使用此格式返回。不泄露:
      - Provider 内部堆栈
      - 上游 API 原始响应体
      - 数据库 / Redis 连接字符串
      - 任何可被利用的内部信息

    使用示例::

        {
            "code": "AUTH_FAILED",
            "message": "Ahrefs API Key 验证失败，请检查授权配置",
            "details": [
                {"field": "api_key", "reason": "返回 HTTP 401"}
            ],
            "trace_id": "req_abc123"
        }

    Attributes:
        code: 标准化错误码，调用方用于程序判断。
        message: 人类可读的错误摘要。
        details: 可选的子错误列表（如多个字段校验失败）。
        trace_id: 请求追踪 ID，用于排查。
        retry_after: 速率限制场景下的建议等待秒数（仅 RATE_LIMITED 使用）。
    """

    code: ErrorCode
    message: str
    details: list[ErrorDetail] = []
    trace_id: str | None = None
    retry_after: int | None = None


# ============================================================
# 对内：Provider 内部异常（不直接对外暴露）
# ============================================================

class InternalAdapterError(Exception):
    """对内异常的根类。

    仅 Provider / Facade / Core 层内部使用。
    携带完整的调试信息，在 server.py 的 exception handler 中被捕获
    并转换为 ErrorResponse 返回。

    Attributes:
        message: 内部错误描述。
        error_code: 对应的对外错误码。
        provider: 触发异常的数据源名称。
        debug_context: 调试用上下文（原始响应、请求参数、重试次数等）。
            **注意**：此字段绝不对外暴露，仅用于日志和内部排查。
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        provider: str | None = None,
        debug_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.provider = provider
        self.debug_context = debug_context or {}


# ============================================================
# 对内：具体异常子类
# ============================================================

class InternalConfigError(InternalAdapterError):
    """配置错误 — 缺失必需配置项或配置格式不正确。

    不可自动恢复，需人工检查 .env / 环境变量。
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, error_code=ErrorCode.INTERNAL_ERROR, **kwargs)


class InternalAuthError(InternalAdapterError):
    """授权错误 — API Key 无效、过期或权限不足。

    HTTP 状态: 401 / 403
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, error_code=ErrorCode.AUTH_FAILED, **kwargs)


class InternalRateLimitError(InternalAdapterError):
    """速率限制 — 被上游限流。

    HTTP 状态: 429
    """

    def __init__(self, message: str, retry_after: int = 60, **kwargs: Any) -> None:
        super().__init__(message, error_code=ErrorCode.RATE_LIMITED, **kwargs)
        self.retry_after = retry_after


class InternalProviderRequestError(InternalAdapterError):
    """网络层错误 — 连接失败、DNS 解析失败、超时等。

    HTTP 状态: 502 / 504
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(
            message,
            error_code=ErrorCode.UPSTREAM_TIMEOUT,
            **kwargs,
        )


class InternalProviderResponseError(InternalAdapterError):
    """响应解析错误 — 上游返回非预期格式或关键字段缺失。

    HTTP 状态: 502
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(
            message,
            error_code=ErrorCode.UPSTREAM_ERROR,
            **kwargs,
        )


class InternalProviderServerError(InternalAdapterError):
    """上游服务端错误 — HTTP 5xx。

    HTTP 状态: 502
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(
            message,
            error_code=ErrorCode.UPSTREAM_ERROR,
            **kwargs,
        )


class InternalDataNotAvailableError(InternalAdapterError):
    """数据不可用 — 所请求数据域当前无数据。

    常见原因:
      - 项目未验证（Site Audit 页面级数据受限）
      - 尚未完成首次抓取
      - 关键词/排名数据正在处理中
      - 查询范围超出 API 可返回范围
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(
            message,
            error_code=ErrorCode.DATA_NOT_AVAILABLE,
            **kwargs,
        )


class InternalQuotaExhaustedError(InternalAdapterError):
    """额度耗尽 — 当前计费周期的 API Units 已用完。

    携带下次重置时间，供 Facade 决策降级策略。
    """

    def __init__(
        self,
        message: str,
        reset_at: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            error_code=ErrorCode.QUOTA_EXHAUSTED,
            **kwargs,
        )
        self.reset_at = reset_at


# ============================================================
# 对内异常 → 对外 ErrorResponse 的转换函数
# ============================================================

def to_error_response(
    exc: InternalAdapterError,
    trace_id: str | None = None,
) -> ErrorResponse:
    """将对内异常消毒为对外 ErrorResponse。

    此函数在 FastAPI exception handler 中调用，确保:
      1. debug_context 绝不泄露到响应体
      2. 错误消息使用对外友好的语言
      3. 所有 ErrorCode 都有对应的 HTTP 状态码

    Args:
        exc: 内部异常实例。
        trace_id: 请求追踪 ID。

    Returns:
        消毒后的 ErrorResponse。
    """
    return ErrorResponse(
        code=exc.error_code,
        message=exc.message,
        trace_id=trace_id,
        retry_after=getattr(exc, "retry_after", None),
    )


# ============================================================
# 便捷工厂函数（Provider 层快速创建异常）
# ============================================================

def auth_error(provider: str, status: int, body: str) -> InternalAuthError:
    """创建授权错误。"""
    return InternalAuthError(
        message=f"{provider} API Key 验证失败（HTTP {status}）",
        provider=provider,
        debug_context={"http_status": status, "response_body": body[:500]},
    )


def rate_limit_error(
    provider: str,
    retry_after: int = 60,
    headers: dict | None = None,
) -> InternalRateLimitError:
    """创建速率限制错误。"""
    return InternalRateLimitError(
        message=f"{provider} 请求频率过高，建议 {retry_after}s 后重试",
        retry_after=retry_after,
        provider=provider,
        debug_context={"response_headers": headers or {}},
    )


def upstream_error(
    provider: str,
    endpoint: str,
    status: int,
    body: str,
) -> InternalProviderServerError:
    """创建上游服务端错误。"""
    return InternalProviderServerError(
        message=f"{provider} 上游服务返回 HTTP {status}（{endpoint}）",
        provider=provider,
        debug_context={
            "endpoint": endpoint,
            "http_status": status,
            "response_body": body[:500],
        },
    )


def data_not_available(
    provider: str,
    capability: str,
    reason: str,
) -> InternalDataNotAvailableError:
    """创建数据不可用错误。"""
    return InternalDataNotAvailableError(
        message=f"{provider} 的 {capability} 数据暂不可用：{reason}",
        provider=provider,
        debug_context={"capability": capability, "reason": reason},
    )


def quota_exhausted(
    provider: str,
    reset_at: str | None = None,
) -> InternalQuotaExhaustedError:
    """创建额度耗尽错误。"""
    return InternalQuotaExhaustedError(
        message=f"{provider} API Units 已耗尽{f'，预计 {reset_at} 重置' if reset_at else ''}",
        reset_at=reset_at,
        provider=provider,
    )
