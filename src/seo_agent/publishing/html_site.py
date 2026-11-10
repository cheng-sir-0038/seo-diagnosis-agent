from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import aiohttp
from pydantic import BaseModel, Field, field_validator

from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind, Status
from src.seo_agent.adapters.base import AdapterHealth, AdapterStatus, BaseAdapter

_SAFE_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$")


class HtmlSiteSettings(BaseModel):
    root_path: Path
    base_url: str
    content_subdir: str = ""
    request_timeout: float = 15.0

    @field_validator("content_subdir")
    @classmethod
    def validate_subdir(cls, value: str) -> str:
        normalized = value.strip("/ ")
        if not normalized:
            return ""
        parts = Path(normalized).parts
        if any(part in {".", ".."} for part in parts):
            raise ValueError("content_subdir 不能包含路径穿越")
        return normalized


class HtmlPublishRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    html_content: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SAFE_SLUG.fullmatch(normalized):
            raise ValueError("slug 只允许小写字母、数字和中划线")
        return normalized


class HtmlPublishResult(BaseModel):
    success: bool
    adapter_key: str = "html_site"
    file_path: str | None = None
    published_url: str | None = None
    checksum: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class PublishVerification(BaseModel):
    reachable: bool
    status_code: int | None = None
    error_message: str | None = None


class HtmlSitePublishAdapter(BaseAdapter):
    key = "html_site"
    category = "publisher"

    def __init__(
        self,
        settings: HtmlSiteSettings,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.settings = settings
        self.observability = observability

    def capabilities(self) -> set[str]:
        return {"publish_html", "verify_url"}

    async def health_check(self) -> AdapterHealth:
        try:
            root = self.settings.root_path.resolve()
            root.mkdir(parents=True, exist_ok=True)
            writable = os.access(root, os.W_OK)
        except OSError as exc:
            return AdapterHealth(status=AdapterStatus.UNAVAILABLE, message=str(exc))
        if not self.settings.base_url.strip():
            return AdapterHealth(
                status=AdapterStatus.NOT_CONFIGURED,
                message="HTML_SITE_BASE_URL 未配置",
            )
        return AdapterHealth(
            status=AdapterStatus.READY if writable else AdapterStatus.UNAVAILABLE,
            message=None if writable else "HTML 站点目录不可写",
        )

    async def publish(self, request: HtmlPublishRequest) -> HtmlPublishResult:
        arguments = {
            "slug": request.slug,
            "content_length": len(request.html_content),
            "idempotency_key": request.idempotency_key,
        }
        started = time.perf_counter()
        async with observed_operation(
            self.observability,
            "html_site.publish",
            kind=SpanKind.ADAPTER,
            component_name=self.key,
            input_data=arguments,
            trigger_type="publish",
        ) as span:
            try:
                root = self.settings.root_path.resolve()
                target_dir = (root / self.settings.content_subdir).resolve()
                if not target_dir.is_relative_to(root):
                    raise ValueError("发布目录超出 HTML 站点根目录")
                target_dir.mkdir(parents=True, exist_ok=True)
                target = (target_dir / f"{request.slug}.html").resolve()
                if not target.is_relative_to(root):
                    raise ValueError("slug 导致路径越界")
                payload = request.html_content.encode("utf-8")
                checksum = hashlib.sha256(payload).hexdigest()
                await asyncio.to_thread(self._atomic_write, target, payload)
                url_parts = [self.settings.base_url.rstrip("/")]
                if self.settings.content_subdir:
                    url_parts.append(quote(self.settings.content_subdir.strip("/"), safe="/"))
                url_parts.append(f"{quote(request.slug)}.html")
                result = HtmlPublishResult(
                    success=True,
                    file_path=str(target),
                    published_url="/".join(url_parts),
                    checksum=checksum,
                )
            except Exception as exc:
                if span is not None:
                    await span.tool_call(
                        tool_call_id=f"publish-{uuid.uuid4().hex}",
                        tool_name="html_site.publish",
                        adapter_key=self.key,
                        operation="publish",
                        arguments=arguments,
                        status=Status.FAILED,
                        latency_ms=round((time.perf_counter() - started) * 1000, 3),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                    await span.capture_exception(
                        exc,
                        issue_type="HTML_PUBLISH_FAILED",
                        component=self.key,
                    )
                raise
            if span is not None:
                await span.tool_call(
                    tool_call_id=f"publish-{uuid.uuid4().hex}",
                    tool_name="html_site.publish",
                    adapter_key=self.key,
                    operation="publish",
                    arguments=arguments,
                    result_summary={
                        "published_url": result.published_url,
                        "checksum": result.checksum,
                    },
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
            return result

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    async def verify(self, published_url: str) -> PublishVerification:
        started = time.perf_counter()
        async with observed_operation(
            self.observability,
            "html_site.verify",
            kind=SpanKind.ADAPTER,
            component_name=self.key,
            input_data={"published_url": published_url},
            trigger_type="monitoring",
        ) as span:
            timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(published_url, allow_redirects=True) as response:
                        result = PublishVerification(
                            reachable=200 <= response.status < 400,
                            status_code=response.status,
                        )
            except (aiohttp.ClientError, TimeoutError) as exc:
                result = PublishVerification(reachable=False, error_message=str(exc))
            if span is not None:
                status = Status.COMPLETED if result.reachable else Status.FAILED
                await span.tool_call(
                    tool_call_id=f"verify-{uuid.uuid4().hex}",
                    tool_name="html_site.verify",
                    adapter_key=self.key,
                    operation="verify",
                    arguments={"published_url": published_url},
                    result_summary={
                        "reachable": result.reachable,
                        "status_code": result.status_code,
                    },
                    status=status,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    error_code=None if result.reachable else "URL_NOT_REACHABLE",
                    error_message=result.error_message,
                )
                if not result.reachable:
                    await span.capture_exception(
                        RuntimeError(result.error_message or "发布 URL 不可访问"),
                        issue_type="PUBLISH_URL_NOT_REACHABLE",
                        component=self.key,
                        error_code="URL_NOT_REACHABLE",
                    )
            return result
