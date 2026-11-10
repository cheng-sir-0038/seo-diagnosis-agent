"""
全局配置管理

使用 pydantic-settings 从环境变量 / .env 文件加载配置。
支持开发、测试、生产三种环境。

使用方式::

    from src.core.config import settings
    print(settings.ahrefs_api_base_url)

环境变量优先级: .env.local > .env > 系统环境变量 > 默认值
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。

    所有配置项均可通过环境变量覆盖，命名规则为 UPPERCASE。
    敏感信息（API Key 等）通过 .env.local 注入，不提交到版本控制。

    Attributes:
        project_name: 项目名称，用于 API 文档标题。
        environment: 运行环境：development / test / production。
        debug: 是否开启调试模式。
        host: FastAPI 服务绑定地址。
        port: FastAPI 服务绑定端口。

        redis_url: Redis 连接地址，用于短期热缓存（TTL 自动过期）。
        sqlite_path: SQLite 数据库文件路径，用于证据快照和历史数据持久化。
        cache_default_ttl: 缓存默认过期时间（秒）。

        ahrefs_api_base_url: Ahrefs API v3 基础地址。
        ahrefs_api_key: Ahrefs API Key（敏感信息，从环境变量注入）。
        ahrefs_rate_limit: Ahrefs API 速率限制（请求/分钟）。
        ahrefs_request_timeout: 单次请求超时时间（秒）。

        dataforseo_default_limit: DataForSEO 单次查询默认返回条数。

        log_level: 日志级别。
        log_format: 日志输出格式。
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 项目基本信息 ----
    project_name: str = "SEO Diagnosis Agent"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = True

    # ---- 服务绑定 ----
    host: str = "127.0.0.1"
    port: int = 8000

    # ---- 缓存与存储 ----
    redis_url: str = "redis://localhost:6379/0"
    sqlite_path: Path = Path("data/evidence.db")
    cache_default_ttl: int = 300  # 5 分钟

    # ---- Serper.dev API (实时 Google SERP) ----
    serper_api_base_url: str = "https://google.serper.dev"
    serper_api_key: str = ""  # ← 从 .env.local 注入，注册地址: https://serper.dev
    serper_request_timeout: float = 30.0
    serper_max_retries: int = 2
    serper_default_language: str = "en"

    # ---- Ahrefs API ----
    ahrefs_api_base_url: str = "https://api.ahrefs.com/v3"
    ahrefs_api_key: str = ""  # ← 从 .env.local 注入
    ahrefs_rate_limit: int = 50  # 保守值，实际限制为 60 req/min
    ahrefs_request_timeout: int = 30  # 秒

    # ---- DataForSEO API ----
    dataforseo_api_base_url: str = "https://api.dataforseo.com"
    dataforseo_login: str = ""  # ← API 登录邮箱
    dataforseo_api_key: str = ""  # ← API 密钥
    dataforseo_rate_limit: int = 30  # DataForSEO 默认 30 req/min
    dataforseo_default_limit: int = 2  # 真实验收默认只取少量记录

    # ---- Google PageSpeed Insights API v5 ----
    pagespeed_api_base_url: str = "https://pagespeedonline.googleapis.com/pagespeedonline/v5"
    pagespeed_api_key: str = ""  # 正式 live 模式必填，从 .env.local 注入
    pagespeed_rate_limit: int = 10
    pagespeed_request_timeout: float = 120.0
    pagespeed_max_retries: int = 2
    pagespeed_default_strategy: Literal["mobile", "desktop"] = "mobile"
    pagespeed_default_locale: str = "zh-CN"
    pagespeed_max_opportunities: int = 10

    # ---- Direct page HTML metadata ----
    page_metadata_request_timeout: float = 20.0
    page_metadata_max_response_bytes: int = 2_000_000
    page_metadata_max_redirects: int = 5
    page_metadata_user_agent: str = "SEO-Agent-MetadataBot/1.0"

    # ---- python-seo-analyzer isolated runtime ----
    python_seo_analyzer_enabled: bool = False
    python_seo_analyzer_python: str = ""
    python_seo_analyzer_timeout: float = 8.0
    python_seo_analyzer_max_output_bytes: int = 1_000_000

    # ---- GSC / GA4 配置预留：当前没有对应数据源实现 ----
    gsc_client_id: str = ""
    gsc_client_secret: str = ""
    gsc_refresh_token: str = ""

    ga4_property_id: str = ""
    ga4_credentials_file: str = ""

    # ---- Agent 模型 ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_request_timeout: float = 60.0
    agent_max_iterations: int = 8
    agent_max_tool_calls: int = 12
    agent_tool_timeout: float = 15.0
    agent_context_message_limit: int = 24
    agent_context_character_limit: int = 50_000
    agent_chat_recovery_enabled: bool = True

    # ---- SEO Agent persistence ----
    seo_agent_sqlite_path: Path = Path("data/seo-agent.db")
    langgraph_checkpoint_sqlite_path: Path = Path("data/langgraph-checkpoints.db")
    seo_agent_graph_version: str = "v1"
    seo_agent_policy_version: str = "v1"

    # ---- Notification / SMTP ----
    email_provider: str = "smtp"
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    smtp_security: Literal["ssl", "starttls", "none"] = "ssl"
    smtp_username: str = ""
    smtp_auth_code: str = ""
    email_from_address: str = ""
    email_from_name: str = "SEO优化专员"
    approval_reminder_interval_hours: int = 24
    local_scheduler_enabled: bool = True

    # ---- HTML publisher / monitoring ----
    html_site_root_path: Path = Path("data/html-site")
    html_site_base_url: str = "http://localhost:8080"
    html_site_content_subdir: str = ""
    html_site_request_timeout: float = 15.0
    monitoring_indexing_days: str = "3,7,14,28"
    monitoring_ranking_weeks: int = 8

    # ---- Automation workers ----
    notification_max_attempts: int = 3
    notification_outbox_interval_minutes: int = 1
    automation_schedule_sync_minutes: int = 5

    # ---- Agent Observability Core ----
    observability_sqlite_path: Path = Path("data/agent-observability.db")
    observability_project_key: str = "seo-agent"
    observability_project_name: str = "SEO Agent"
    observability_service_name: str = "seo-agent-api"
    observability_jsonl_enabled: bool = False
    observability_jsonl_path: Path = Path("data/observability/events.jsonl")
    observability_otlp_endpoint: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ---- 日志 ----
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: str = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # ============================================================
    # 衍生属性 / 便捷方法
    # ============================================================

    @property
    def is_production(self) -> bool:
        """是否为生产环境。"""
        return self.environment == "production"

    @property
    def ahrefs_headers(self) -> dict[str, str]:
        """Ahrefs API 公共请求头。

        Returns:
            包含 Authorization 和 Content-Type 的 dict。
        """
        return {
            "Authorization": f"Bearer {self.ahrefs_api_key}",
            "Content-Type": "application/json",
        }

    @property
    def ahrefs_rate_limit_delay(self) -> float:
        """从速率限制反算的单次请求最小间隔（秒）。

        Returns:
            两次请求之间的最小间隔秒数，含 10% 安全系数。
        """
        return 60.0 / (self.ahrefs_rate_limit * 0.9)


# ============================================================
# 全局单例
# ============================================================

settings = Settings()
