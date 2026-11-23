from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext


class ResourceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str | None = Field(default=None, max_length=60)
    age: int | None = Field(default=None, ge=3, le=120)
    completed: bool = False


@dataclass(frozen=True)
class StudyResource:
    id: str
    title: str
    url: str
    topics: tuple[str, ...]
    min_age: int
    max_age: int
    task: str


_RESOURCES = (
    StudyResource(
        "oxford-fractions",
        "Oxford Owl · 分数、小数与百分数", "https://home.oxfordowl.co.uk/maths/primary-fractions/",
        ("分数", "小数", "百分数", "oxford owl"), 5, 11,
        "按年龄选一个活动，练完试着用自己的话说明分数和小数怎样互换。",
    ),
    StudyResource(
        "nrich-age-problem",
        "Cambridge NRICH · How Old Am I?", "https://nrich.maths.org/problems/how-old-am-i",
        ("方程", "代数", "nrich"), 14, 16,
        "先独立列式，遇到困难再打开官网的提示，最后比较自己的思路和示例解法。",
    ),
)


class LearningResources(AgentTool):
    name = "math_resource_lookup"
    description = "按学习主题和年龄推荐 Oxford Owl 或 Cambridge NRICH 的官方活动入口。"
    parameters_schema = ResourceQuery.model_json_schema()

    def __init__(self, completed_ids: list[str] | None = None) -> None:
        self.completed_ids = set(completed_ids or [])

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        query = ResourceQuery.model_validate(arguments)
        matches = [resource for resource in _RESOURCES if (
            not query.topic or any(topic in query.topic.casefold() for topic in resource.topics)
        ) and (query.age is None or resource.min_age <= query.age <= resource.max_age)]
        if query.completed:
            if len(matches) != 1:
                return ToolResult(success=True, data={"answer": "请告诉我你完成了哪一项官方练习。"})
            item = matches[0]
            return ToolResult(success=True, data={
                "answer": f"已记录完成 [{item.title}]({item.url})。",
                "completed_resource_id": item.id,
            })
        links = [
            f"- [{item.title}]({item.url})（{item.min_age}–{item.max_age} 岁）。"
            + ("这一项已记录完成。" if item.id in self.completed_ids else item.task)
            for item in matches
        ]
        answer = (
            "**按主题找官方数学练习**\n\n" + "\n".join(links) if links else
            "暂时没有匹配这组年龄与主题的资料。现有范围是 5–11 岁的分数学习和 14–16 岁的方程探究。"
        )
        return ToolResult(success=True, data={
            "answer": answer, "sources": [item.url for item in matches],
        })
