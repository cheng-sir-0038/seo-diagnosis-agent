from __future__ import annotations

from src.agent.providers.base import ModelProvider
from src.agent.providers.deepseek import DeepSeekProvider
from src.agent.providers.errors import ModelConfigurationError
from src.agent.providers.openai import OpenAIProvider
from src.core.config import Settings, TextModelConfig


def require_text_model(config: Settings) -> TextModelConfig:
    """Resolve the text model once under the same policy used by the settings page."""
    selected = config.text_model()
    if selected is None:
        raise ModelConfigurationError("请在数据源管理中配置 OpenAI 或 DeepSeek 的 API Key。")
    prefix = selected.provider.upper()
    if not selected.api_key.get_secret_value():
        raise ModelConfigurationError(f"{prefix}_API_KEY 未配置。请检查数据源管理。")
    if not selected.model:
        raise ModelConfigurationError(f"{prefix}_MODEL 缺失或无效。请检查数据源管理。")
    if not selected.base_url:
        raise ModelConfigurationError(f"{prefix}_BASE_URL 未配置。请检查服务端设置。")
    return selected


def build_text_provider(config: Settings) -> ModelProvider:
    """Build the selected provider without fallback on upstream errors."""
    selected = require_text_model(config)
    provider = OpenAIProvider if selected.provider == "openai" else DeepSeekProvider
    return provider(
        api_key=selected.api_key.get_secret_value(), base_url=selected.base_url,
        model=selected.model, timeout=selected.timeout,
    )
