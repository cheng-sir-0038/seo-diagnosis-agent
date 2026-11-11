"""只读页面建议的模型输出；引用路径对应本次页面证据。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from src.models.page_metadata import PageMetadataReport


class PageSuggestionEvidence(PageMetadataReport):
    user_supplied: list[Annotated[str, Field(max_length=2000)]] = Field(max_length=5)


class PageSuggestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: PageSuggestionEvidence
    page_identity_url: str = Field(pattern=r"^https?://")


class JsonLdDraft(BaseModel):
    """公共 JSON-LD 结构；业务属性按内容生成，并逐项校验来源。"""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    context: Literal["https://schema.org"] = Field(alias="@context")
    type_name: str = Field(alias="@type", pattern=r"^[A-Z][A-Za-z0-9]{1,79}$")
    url: str = Field(pattern=r"^https?://")


class PageFactCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(
        min_length=1,
        max_length=200,
        description="相对于 page 的 JSON Pointer，例如 /title、/content_excerpt、/user_supplied/0",
    )
    quote: str = Field(min_length=1, max_length=400)


class StructuredFactCitation(PageFactCitation):
    json_pointer: str = Field(min_length=1, max_length=200)


class PageSeoDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_type: str | None = Field(description="有事实依据的 Schema.org 类型；不能确认则为 null")
    page_purpose: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=140)
    description: str = Field(min_length=1, max_length=350)
    keywords: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(max_length=8)
    citations: list[PageFactCitation] = Field(min_length=1, max_length=8)
    image_url: str | None = None
    image_citation: PageFactCitation | None = None
    json_ld: JsonLdDraft | None = Field(description="JSON-LD 对象；类型不能确认则为 null")
    json_ld_citations: list[StructuredFactCitation] = Field(max_length=40)
    missing_information: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        max_length=12
    )
    questions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        max_length=3,
        description="仅包含当前必须回答的事实问题；不追问已明确暂缺的可选信息，可继续时返回空数组",
    )
