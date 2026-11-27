from __future__ import annotations


class ModelConfigurationError(ValueError):
    """A safe, actionable configuration error for model callers."""


class ModelRequestError(RuntimeError):
    """Report only local guidance and allowlisted upstream failure information."""

    def __init__(
        self, provider: str, status_code: int | None = None, *, reason: str = "unavailable",
    ) -> None:
        self.status_code = status_code
        label = "OpenAI" if provider == "openai" else "DeepSeek"
        if status_code == 401:
            detail = "未通过身份验证。请检查 API Key 配置。"
        elif status_code in (403, 404):
            detail = "未能访问配置的模型。请检查模型名称和项目权限。"
        elif status_code == 429 and reason == "insufficient_quota":
            detail = "可用额度不足。请检查账户额度。"
        elif status_code == 429:
            detail = "已达到调用限制。请稍后重试，或检查账户额度。"
        elif status_code in (408, 504) or reason == "timeout":
            detail = "请求超时。请稍后重试。"
        elif status_code == 400:
            detail = "拒绝了模型请求。请检查模型是否支持当前的对话和工具调用。"
        elif reason == "invalid_response":
            detail = "返回的响应不完整或格式无效。请重试。"
        else:
            detail = "模型服务暂时不可用。请检查连接后重试。"
        status = f"（HTTP {status_code}）" if status_code is not None else ""
        super().__init__(f"{label} {detail}{status}")
