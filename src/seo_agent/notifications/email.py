from __future__ import annotations

import asyncio
import smtplib
import time
import uuid
from email.message import EmailMessage as StdlibEmailMessage
from email.utils import formataddr, make_msgid
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind, Status
from src.seo_agent.adapters.base import AdapterHealth, AdapterStatus, BaseAdapter


class SmtpSettings(BaseModel):
    host: str
    port: int
    security: Literal["ssl", "starttls", "none"] = "ssl"
    username: str
    auth_code: str
    from_address: str
    from_name: str = "SEO优化专员"
    timeout: float = 30.0


class EmailMessage(BaseModel):
    message_id: str
    to: list[str] = Field(min_length=1)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str
    text_content: str
    html_content: str | None = None
    idempotency_key: str


class EmailSendResult(BaseModel):
    success: bool
    provider: str
    provider_message_id: str | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class SmtpTransport(Protocol):
    def send(self, settings: SmtpSettings, message: EmailMessage) -> str: ...


class StdlibSmtpTransport:
    def send(self, settings: SmtpSettings, message: EmailMessage) -> str:
        mail = StdlibEmailMessage()
        provider_message_id = make_msgid()
        mail["Message-ID"] = provider_message_id
        mail["From"] = formataddr((settings.from_name, settings.from_address))
        mail["To"] = ", ".join(message.to)
        if message.cc:
            mail["Cc"] = ", ".join(message.cc)
        mail["Subject"] = message.subject
        mail.set_content(message.text_content)
        if message.html_content:
            mail.add_alternative(message.html_content, subtype="html")
        recipients = [*message.to, *message.cc, *message.bcc]
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout)
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
        try:
            client.ehlo()
            if settings.security == "starttls":
                client.starttls()
                client.ehlo()
            client.login(settings.username, settings.auth_code)
            client.send_message(mail, to_addrs=recipients)
        finally:
            client.quit()
        return provider_message_id


class SmtpEmailProvider(BaseAdapter):
    key = "smtp"
    category = "email_provider"

    def __init__(
        self,
        settings: SmtpSettings,
        *,
        transport: SmtpTransport | None = None,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport or StdlibSmtpTransport()
        self.observability = observability

    async def health_check(self) -> AdapterHealth:
        configured = all(
            [
                self.settings.host,
                self.settings.username,
                self.settings.auth_code,
                self.settings.from_address,
            ]
        )
        return AdapterHealth(
            status=AdapterStatus.READY if configured else AdapterStatus.NOT_CONFIGURED,
            message=None if configured else "SMTP 配置不完整",
        )

    def capabilities(self) -> set[str]:
        return {"send_email"}

    async def send(self, message: EmailMessage) -> EmailSendResult:
        arguments = {
            "recipient_count": len(message.to) + len(message.cc) + len(message.bcc),
            "subject": message.subject,
            "idempotency_key": message.idempotency_key,
        }
        started = time.perf_counter()
        async with observed_operation(
            self.observability,
            "smtp.send",
            kind=SpanKind.ADAPTER,
            component_name=self.key,
            input_data=arguments,
            trigger_type="notification",
        ) as span:
            try:
                reference = await asyncio.to_thread(
                    self.transport.send,
                    self.settings,
                    message,
                )
                result = EmailSendResult(
                    success=True,
                    provider=self.key,
                    provider_message_id=reference,
                    status="SENT",
                )
            except (OSError, smtplib.SMTPException) as exc:
                result = EmailSendResult(
                    success=False,
                    provider=self.key,
                    status="FAILED",
                    error_code="SMTP_ERROR",
                    error_message=str(exc),
                    retryable=True,
                )
            if span is not None:
                status = Status.COMPLETED if result.success else Status.FAILED
                await span.tool_call(
                    tool_call_id=f"smtp-{uuid.uuid4().hex}",
                    tool_name="smtp.send_email",
                    adapter_key=self.key,
                    operation="send",
                    arguments=arguments,
                    result_summary={"status": result.status},
                    status=status,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    external_request_id=result.provider_message_id,
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
                if not result.success:
                    await span.capture_exception(
                        RuntimeError(result.error_message or "SMTP 发送失败"),
                        issue_type="SMTP_ERROR",
                        component=self.key,
                        error_code=result.error_code,
                    )
            return result
