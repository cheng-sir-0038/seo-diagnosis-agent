from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from src.core.exceptions import ErrorCode, InternalAdapterError, InternalProviderResponseError
from src.models.geo import GeoCitation, GeoProvider, GeoSearchSample, http_host
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation, record_model_failure
from src.observability.models import SpanKind

SEARCH_INSTRUCTIONS = (
    "Search the public web to answer the user's question. Use the language of the question. "
    "Keep the answer concise and cite the sources where you use them."
)


def _citations(
    text: str,
    annotations: list[dict[str, Any]],
    *,
    nested: bool = False,
) -> list[GeoCitation]:
    by_url: dict[str, GeoCitation] = {}
    for annotation in annotations:
        if annotation.get("type") != "url_citation":
            continue
        data = annotation["url_citation"] if nested else annotation
        url = data.get("url")
        if not isinstance(url, str) or len(url) > 2048 or not http_host(url):
            continue
        start, end = data.get("start_index"), data.get("end_index")
        excerpt = (
            text[start:end]
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)
            else ""
        )
        by_url.setdefault(
            url,
            GeoCitation(
                url=url,
                title=(data.get("title") or url)[:240],
                answer_excerpt=excerpt[:400],
            ),
        )
    return list(by_url.values())


def _usage(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") or {}
    counts = {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
        "total_tokens": usage.get("total_tokens"),
    }
    return {key: value for key, value in counts.items() if type(value) is int}


class GeoSearchAdapter:
    provider: GeoProvider
    endpoint: str

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float,
        *,
        observability: ObservabilityClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key, self.base_url, self.model, self.timeout = api_key, base_url, model, timeout
        self.observability, self.transport = observability, transport

    async def search(self, question: str) -> GeoSearchSample:
        payload = self.payload(question)
        details = {
            "provider": self.provider,
            "model": self.model,
            "operation": "geo_web_search",
            "input_summary": payload,
        }
        async with observed_operation(
            self.observability,
            "geo_web_search",
            kind=SpanKind.MODEL,
            component_name=self.provider,
            input_data=payload,
        ) as span:
            started = time.monotonic()
            try:
                data = await self._request(payload)
                sample = self.parse(data)
            except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
                error = InternalProviderResponseError("联网模型返回了无效的引用样本")
                if span:
                    await record_model_failure(span, error, component=self.provider, **details)
                raise error from exc
            except InternalAdapterError as exc:
                if span:
                    await record_model_failure(span, exc, component=self.provider, **details)
                raise
            if span:
                span.output_data = sample.model_dump(mode="json")
                await span.model_call(
                    **details,
                    **_usage(data),
                    output_summary=span.output_data,
                    latency_ms=round((time.monotonic() - started) * 1000, 3),
                )
            return sample

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}{self.endpoint}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException:
            raise InternalAdapterError(
                "联网模型请求超时",
                error_code=ErrorCode.UPSTREAM_TIMEOUT,
            ) from None
        except httpx.RequestError:
            raise InternalProviderResponseError("联网模型服务暂不可达") from None
        if response.is_error:
            code = {
                401: ErrorCode.AUTH_FAILED,
                402: ErrorCode.QUOTA_EXHAUSTED,
                403: ErrorCode.AUTH_FAILED,
                429: ErrorCode.RATE_LIMITED,
            }.get(response.status_code, ErrorCode.UPSTREAM_ERROR)
            raise InternalAdapterError(
                f"{self.provider} 联网请求失败（HTTP {response.status_code}）",
                error_code=code,
            )
        data = response.json()
        if not isinstance(data, dict) or data.get("error"):
            raise InternalProviderResponseError("联网模型未返回完整答复")
        return data

    def sample(
        self,
        data: dict[str, Any],
        *,
        answer: str,
        citations: list[GeoCitation],
        calls: list[dict[str, Any]],
    ) -> GeoSearchSample:
        completed = [call for call in calls if call.get("status") == "completed"]
        status = "completed" if completed else "failed" if calls else "not_searched"
        queries = list(
            dict.fromkeys(
                query for call in completed for query in _queries(call.get("action") or {})
            )
        )
        return GeoSearchSample(
            provider=self.provider,
            model=data.get("model") or self.model,
            response_id=data.get("id"),
            fetched_at=datetime.now(UTC),
            search_status=status,
            queries=queries,
            answer=answer,
            citations=citations,
        )

    def payload(self, question: str) -> dict[str, Any]:
        raise NotImplementedError

    def parse(self, data: dict[str, Any]) -> GeoSearchSample:
        raise NotImplementedError


def _queries(action: dict[str, Any]) -> list[str]:
    values = action.get("queries") or [action.get("query")]
    return [value for value in values if isinstance(value, str) and value.strip()]


class DeepSeekGeoSearch(GeoSearchAdapter):
    provider: GeoProvider = "deepseek"
    endpoint = "/chat/completions"

    def payload(self, question: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "stream": False,
            "thinking": {"type": "disabled"},
            "tools": [{"type": "deepseek_web_search"}],
            "messages": [
                {"role": "system", "content": SEARCH_INSTRUCTIONS},
                {"role": "user", "content": question},
            ],
        }

    def parse(self, data: dict[str, Any]) -> GeoSearchSample:
        choice = data["choices"][0]
        message = choice["message"]
        answer = message["content"]
        if (
            choice.get("finish_reason") != "stop"
            or not isinstance(answer, str)
            or not answer.strip()
        ):
            raise InternalProviderResponseError("DeepSeek 联网答复未完成")
        return self.sample(
            data,
            answer=answer,
            citations=_citations(answer, message.get("annotations") or [], nested=True),
            calls=[
                call
                for step in message.get("server_tool_calls") or []
                for call in step["calls"]
                if call.get("type") == "deepseek_web_search"
            ],
        )


class OpenAIGeoSearch(GeoSearchAdapter):
    provider: GeoProvider = "openai"
    endpoint = "/responses"

    def payload(self, question: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "store": False,
            "instructions": SEARCH_INSTRUCTIONS,
            "input": question,
            "tools": [{"type": "web_search"}],
            "max_tool_calls": 1,
        }

    def parse(self, data: dict[str, Any]) -> GeoSearchSample:
        if data.get("status") != "completed":
            raise InternalProviderResponseError("OpenAI 联网答复未完成")
        output = data["output"]
        blocks = [
            part
            for item in output
            if item.get("type") == "message"
            for part in item["content"]
            if part.get("type") == "output_text"
        ]
        answer = "\n".join(part["text"] for part in blocks)
        if not answer.strip():
            raise InternalProviderResponseError("OpenAI 联网答复没有文本")
        citations = {
            citation.url: citation
            for block in blocks
            for citation in _citations(block["text"], block.get("annotations") or [])
        }
        return self.sample(
            data,
            answer=answer,
            citations=list(citations.values()),
            calls=[item for item in output if item.get("type") == "web_search_call"],
        )
