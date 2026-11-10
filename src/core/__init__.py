"""核心基础设施模块。"""

from src.core.config import Settings, settings
from src.core.exceptions import (
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    InternalAdapterError,
    InternalAuthError,
    InternalConfigError,
    InternalDataNotAvailableError,
    InternalProviderRequestError,
    InternalProviderResponseError,
    InternalProviderServerError,
    InternalQuotaExhaustedError,
    InternalRateLimitError,
    auth_error,
    data_not_available,
    quota_exhausted,
    rate_limit_error,
    to_error_response,
    upstream_error,
)
from src.core.cache import CacheManager, cache_manager

__all__ = [
    # Config
    "Settings",
    "settings",
    # External error model
    "ErrorCode",
    "ErrorDetail",
    "ErrorResponse",
    # Internal exceptions
    "InternalAdapterError",
    "InternalAuthError",
    "InternalConfigError",
    "InternalDataNotAvailableError",
    "InternalProviderRequestError",
    "InternalProviderResponseError",
    "InternalProviderServerError",
    "InternalQuotaExhaustedError",
    "InternalRateLimitError",
    # Exception factories
    "auth_error",
    "data_not_available",
    "quota_exhausted",
    "rate_limit_error",
    "to_error_response",
    "upstream_error",
    # Cache
    "CacheManager",
    "cache_manager",
]
