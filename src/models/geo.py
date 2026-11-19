from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

GeoProvider = Literal["deepseek", "openai"]


def http_host(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme in {"https", "http"} and not parsed.username and not parsed.password:
            return (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        pass
    return ""


class GeoContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class GeoCitationRequest(GeoContract):
    question: str = Field(min_length=1, max_length=240)
    target_domain: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.-]+$")
    provider: GeoProvider = "deepseek"


class GeoCitation(GeoContract):
    url: str = Field(max_length=2048)
    title: str = Field(max_length=240)
    answer_excerpt: str = Field(default="", max_length=400)

    @field_validator("url")
    @classmethod
    def validate_url(cls, url: str) -> str:
        if not http_host(url):
            raise ValueError("引用须为有效的 HTTP(S) URL")
        return url


class GeoSearchSample(GeoContract):
    provider: GeoProvider
    model: str
    response_id: str | None = None
    fetched_at: AwareDatetime
    search_status: Literal["completed", "not_searched", "failed"]
    queries: list[str]
    answer: str
    citations: list[GeoCitation] = Field(max_length=50)


class GeoCitationReport(GeoContract):
    request: GeoCitationRequest
    sample: GeoSearchSample

    citation_status: Literal["cited", "not_cited", "no_citations", "not_searched", "search_failed"]
    matching_urls: list[str]

    @classmethod
    def build(cls, request: GeoCitationRequest, sample: GeoSearchSample) -> "GeoCitationReport":
        target = request.target_domain.lower().removeprefix("www.").rstrip(".")
        matches = [
            citation.url
            for citation in sample.citations
            if (host := http_host(citation.url)) == target or host.endswith(f".{target}")
        ]

        if sample.search_status != "completed":
            status = "search_failed" if sample.search_status == "failed" else "not_searched"
            matches = []
        elif not sample.citations:
            status = "no_citations"
        else:
            status = "cited" if matches else "not_cited"
        return cls(request=request, sample=sample, citation_status=status, matching_urls=matches)


class GeoQuestionSelection(GeoContract):
    question_id: int | None = Field(ge=1)
    reason: str = Field(min_length=1, max_length=240)


class GeoContentEdit(GeoContract):
    gap: str = Field(min_length=1, max_length=240)
    draft: str = Field(min_length=1, max_length=600)
    page_quote: str = Field(min_length=1, max_length=200)
    citation_id: int = Field(ge=1)


class GeoContentDraft(GeoContract):
    edits: list[GeoContentEdit] = Field(max_length=3)
    needs_verification: list[str] = Field(max_length=3)
