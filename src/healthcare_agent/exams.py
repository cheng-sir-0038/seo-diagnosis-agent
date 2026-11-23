from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from src.agent.tools.base import ToolResult, ToolRuntimeContext
from src.healthcare_agent.results import (
    EvidenceSource,
    HealthcareTool,
    RuleStatus,
    ask,
    resumed_arguments,
)


class PublicRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    title: str
    source_url: str
    year: int
    applies_to: tuple[str, ...]
    quote: str


_REGISTRATION = PublicRule(
    title="2026年国家医师资格考试湖北考区公告",
    source_url="https://www.hbwsrc.cn/news/show-1830.html",
    year=2026,
    applies_to=("湖北考区医师资格考试",),
    quote="但仍需完成考试报名整个流程(含试用期申报、国网报名、资格审核等)",
)
_SYLLABUS = PublicRule(
    title="国家卫生健康委员会医师资格考试委员会关于修订《医师资格考试大纲"
    "（医学综合考试）》医学人文部分内容的通知",
    source_url="https://www1.nmec.org.cn/Pages/ArticleInfo-4-11903.html",
    year=2026,
    applies_to=(
        "临床执业（助理）医师",
        "口腔执业（助理）医师",
        "公共卫生执业（助理）医师",
        "乡村全科执业助理医师",
    ),
    quote="《医师资格考试大纲（医学综合考试2024年版）》中的医学人文部分进行了修订。",
)

_SYLLABUS_CATEGORIES = {"临床", "口腔", "公共卫生", "乡村全科", *_SYLLABUS.applies_to}
for _category in ("临床", "口腔", "公共卫生"):
    _SYLLABUS_CATEGORIES.update(f"{_category}{name}" for name in ("执业医师", "执业助理医师"))
_HUBEI = {"湖北", "湖北省", "湖北考区", "武汉", "武汉市", "湖北武汉", "武汉考点", "湖北省武汉市"}
_PHYSICIAN_EXAMS = {
    "医师资格",
    "医师资格考试",
    "国家医师资格考试",
    "执业医师",
    "执业助理医师",
    "乡村全科执业助理医师",
    *(
        f"{category}{qualification}"
        for category in ("临床", "口腔", "公共卫生", "中医", "中西医结合")
        for qualification in ("执业医师", "执业助理医师", "执业（助理）医师")
    ),
}


class SyllabusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    year: int | None = Field(default=None, description="用户要核对的版次年份，省略则列示收录的版本")
    category: str | None = Field(default=None, max_length=80, description="课程或考生的考试类别")


class MedicalExamSyllabus(HealthcareTool):
    name = "medical_exam_syllabus"
    description = "返回医学人文大纲修订的官方范围、版次和原文，可按课程类别核对是否适用。"
    parameters_schema = SyllabusInput.model_json_schema()

    async def execute(
        self,
        arguments: dict,
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = SyllabusInput.model_validate(arguments)
        if options.year not in (None, _SYLLABUS.year):
            status = RuleStatus.OUTSIDE_SCOPE
            conclusion = "这份通知说明 2026 年修订情况，查询的版次需要另找对应公告。"
        elif options.category in {"护士", "护理", "护理学", "护士资格", "护士执业资格", "执业药师"}:
            status = RuleStatus.OUTSIDE_SCOPE
            conclusion = "这份医师考纲通知列出的考试类别未包含所问资格，请查该资格的考纲。"
        elif options.category and options.category not in _SYLLABUS_CATEGORIES:
            status = RuleStatus.UNCONFIRMED
            conclusion = "请对照通知列出的四类考试核实课程类别，再确定适用范围。"
        else:
            status = RuleStatus.MATCHED
            conclusion = "2026 年修订了适用类别的医学综合考试医学人文部分。"
        evidence = _SYLLABUS.model_dump(mode="json")
        return self.cards(
            "医学人文考纲适用范围",
            "国家医学考试网",
            conclusion,
            [
                {
                    "key": "scope",
                    "title": evidence["title"],
                    "url": evidence["source_url"],
                    "summary": evidence["quote"],
                    "metadata": [
                        {"label": "适用考试", "value": "、".join(_SYLLABUS.applies_to)},
                        {"label": "发布日", "value": "2026-01-26"},
                    ],
                }
            ],
            status=status,
            source_kind=EvidenceSource.OFFICIAL_EXCERPT,
            evidence=evidence,
        )


def public_rule_evidence(tool_name: str, result: ToolResult | dict) -> dict[str, Any] | None:
    """Export only the verified syllabus clause from a completed healthcare tool call."""
    if tool_name != MedicalExamSyllabus.name:
        return None
    payload = result.model_dump(mode="json") if isinstance(result, ToolResult) else result
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if payload.get("success") is not True or not isinstance(data, dict):
        return None
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict) or payload.get("error_code") or metadata.get("human_input"):
        return None
    if (
        data.get("status") != RuleStatus.MATCHED
        or data.get("source_kind") != EvidenceSource.OFFICIAL_EXCERPT
    ):
        return None
    record = data.get("evidence")
    if not isinstance(record, dict):
        return None
    evidence = {field: record.get(field) for field in PublicRule.model_fields}
    if evidence != _SYLLABUS.model_dump(mode="json"):
        return None
    return deepcopy(evidence)


class RegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    topic: Literal["registration", "skills_retake"] = Field(
        default="registration",
        description="报名流程或技能考试免考条件",
    )
    year: int | None = Field(default=None, description="想报考的年份")
    region: str | None = Field(default=None, max_length=80, description="报考省份或考区")
    exam: str | None = Field(
        default=None,
        max_length=80,
        description="考试名称，如医师资格或护士资格",
    )
    skills_year: int | None = Field(default=None, description="上次参加技能考试的年份")
    skills_passed: StrictBool | Literal["unknown"] | None = Field(
        default=None,
        description="上次技能考试是否合格，未说明时留空",
    )
    comprehensive_passed: StrictBool | Literal["unknown"] | None = Field(
        default=None,
        description="上次医学综合考试是否通过",
    )
    national_base: StrictBool | Literal["unknown"] | None = Field(
        default=None,
        description="上次考点是否为国家实践技能考试基地",
    )


_QUESTIONS = {
    "year": "你想核对哪一年的报考规则？",
    "region": "你要报哪个省份或考区？",
    "exam": "你报考的是哪项资格考试？",
    "skills_year": "你上次参加医师实践技能考试是哪一年？",
    "skills_passed": "你那次实践技能考试合格了吗？",
    "comprehensive_passed": "你同年的医学综合考试通过了吗？",
    "national_base": "你在国家实践技能考试基地参加了这次技能考试吗？",
}


def _exam_reply(field: str, answer: str) -> object | None:
    text = answer.strip(" 。，！")
    if field in {"year", "skills_year"}:
        return int(text[:4]) if re.fullmatch(r"[0-9]{4}年?", text) else None
    if field == "region":
        return text if text in _HUBEI else None
    if field == "exam":
        return text if text in _PHYSICIAN_EXAMS or text in {"护士资格", "执业药师"} else None
    if text in {"是", "是的", "对", "否", "不", "不是", "不是的"}:
        return text in {"是", "是的", "对"}
    if field in {"skills_passed", "comprehensive_passed"}:
        if text in {"过了", "通过了", "合格", "没过", "没通过", "不合格"}:
            return text in {"过了", "通过了", "合格"}
    return None


class MedicalExamRegistration(HealthcareTool):
    name = "medical_exam_registration"
    description = (
        "核对指定年份、考区、考试名称的报名流程或实践技能免考条件。"
        "只传用户已说明的条件，未知字段留空，由工具询问。"
    )
    parameters_schema = RegistrationInput.model_json_schema()

    async def execute(
        self,
        arguments: dict,
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        arguments, clarification = resumed_arguments(self.name, arguments, runtime, self._reply)
        if clarification:
            return clarification
        options = RegistrationInput.model_validate(arguments)
        facts = options.model_dump(mode="json")
        if (
            options.year not in (None, 2026)
            or options.region
            and options.region not in _HUBEI
            or options.exam
            and options.exam not in _PHYSICIAN_EXAMS
        ):
            return self._result(
                RuleStatus.OUTSIDE_SCOPE,
                "本次收录的公告仅覆盖 2026 年湖北考区医师资格考试。",
                section="公告适用范围",
                quote="结合湖北考区实际",
            )
        for field in ("year", "region", "exam"):
            if not facts[field]:
                return ask(field, _QUESTIONS[field], facts)
        if options.topic == "registration":
            return self._result(
                RuleStatus.MATCHED,
                "2026 年湖北考区网上报名为 1 月 29 日 10 时至 2 月 11 日 24 时。"
                "资格审核为 2 月 27 日至 3 月 12 日，审核方式按当地考点通知。",
                section="一、考试报名",
                quote="考试报名包括网上报名和资格审核两个部分。",
            )
        if options.skills_year not in (None, 2025):
            return self._result(
                RuleStatus.OUTSIDE_SCOPE, "本条技能免考规定针对 2025 年参加技能考试的考生。"
            )
        if (
            options.skills_passed is False
            or options.comprehensive_passed is True
            or (options.national_base is False)
        ):
            return self._result(
                RuleStatus.NOT_EXEMPT,
                "已有条件不符合公告中这项技能免考规定。",
                exempt=False,
            )
        for field in ("skills_year", "skills_passed", "comprehensive_passed", "national_base"):
            if facts[field] in (None, "unknown"):
                return ask(field, _QUESTIONS[field], facts)
        return self._result(
            RuleStatus.MATCHED,
            "按所述条件，可免 2026 年实践技能考试及该项费用。仍须完成报名和资格审核，"
            "并按时缴纳医学综合考试费。",
            exempt=True,
            registration_required=True,
        )

    def _reply(self, field: str, answer: str) -> object | None:
        if answer.strip(" 。！") in {"不清楚", "不知道", "不确定", "待核实", "不记得", "记不清"}:
            if field in {"region", "exam", "year"}:
                return self._result(
                    RuleStatus.UNCONFIRMED,
                    "报考范围仍待核实。目前可核对 2026 年湖北考区医师资格考试。",
                    section="公告适用范围",
                    quote="结合湖北考区实际",
                )
            return self._result(
                RuleStatus.UNCONFIRMED,
                "仍有条件待核实。只有 2025 年在国家实践技能考试基地参加技能考试"
                "并合格、且未通过医学综合考试的考生，才符合本条免考规定。",
            )
        return _exam_reply(field, answer)

    def _result(
        self,
        status: RuleStatus,
        conclusion: str,
        *,
        section: str = "二、考试缴费（缴费方式第 2 条）",
        quote: str = _REGISTRATION.quote,
        **facts: Any,
    ) -> ToolResult:
        rule = _REGISTRATION
        return self.cards(
            "医师资格报考核对",
            "湖北卫生人才网",
            conclusion,
            [
                {
                    "key": "notice",
                    "title": rule.title,
                    "url": rule.source_url,
                    "summary": quote,
                    "metadata": [
                        {"label": "出处", "value": section},
                        {"label": "发布日", "value": "2026-01-28"},
                    ],
                }
            ],
            status=status,
            source_kind=EvidenceSource.OFFICIAL_EXCERPT,
            evidence={**rule.model_dump(mode="json"), "section": section, "quote": quote},
            **facts,
        )
