from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext

SUPPORTED_YEAR = 2026
_PROVINCE = "湖北"
_SUBJECT = "初中数学"
_OFFICIAL_NOTICES = "https://www.hbea.edu.cn/html/jszg/index.html"
_EXAM_PORTAL = "https://ntce.neea.edu.cn/"
_SUBJECTS = "https://ntce.neea.edu.cn/xhtml1/report/2307/302-1.htm"
_SYLLABUS = "https://ntce.neea.edu.cn/html1/report/1508/347-1.htm"


class ExamSession(StrEnum):
    FIRST = "first"
    SECOND = "second"


class TeacherExamQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    region: str | None = Field(default=None, max_length=50)
    year: int | None = None
    session: ExamSession | None = None
    subject: str | None = Field(default=None, max_length=60)

    @property
    def outside_coverage(self) -> bool:
        return (
            bool(self.region and self.region.removesuffix("省") != _PROVINCE)
            or (self.year is not None and self.year != SUPPORTED_YEAR)
            or bool(self.subject and self.subject != _SUBJECT)
        )

    def clarification(self) -> str | None:
        if self.outside_coverage:
            return None
        missing = [label for value, label in (
            (self.region, "省份"), (self.year, "报考年份"), (self.session, "上半年或下半年"),
            (self.subject, "学段和学科"),
        ) if not value]
        return f"请补充{'、'.join(missing)}，我按对应公告核对。" if missing else None


@dataclass(frozen=True)
class RegistrationNotice:
    label: str
    url: str
    registration: str
    payment: str
    admission_ticket: str
    exam_date: str


_NOTICES = {
    ExamSession.FIRST: RegistrationNotice(
        "上半年", "https://www.hbea.edu.cn/html/2025-12/15603.html",
        "1 月 9 日 10:00 至 1 月 12 日 16:00", "1 月 14 日 24:00", "3 月 2 日至 7 日", "3 月 7 日",
    ),
    ExamSession.SECOND: RegistrationNotice(
        "下半年", "https://www.hbea.edu.cn/html/2026-06/15957.html",
        "7 月 3 日 10:00 至 7 月 6 日 16:00", "7 月 8 日 24:00", "9 月 7 日至 12 日", "9 月 12 日",
    ),
}


class TeacherWrittenExam(AgentTool):
    name = "ntce_written_exam_lookup"
    description = "按省份、年份、考期和学科查询湖北初中数学教资笔试的公告、科目和大纲。"
    parameters_schema = TeacherExamQuery.model_json_schema()

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        query = TeacherExamQuery.model_validate(arguments)
        scope = query.model_dump(mode="json")
        if query.outside_coverage or query.session is None or query.clarification():
            answer = (
                f"现有资料覆盖 {SUPPORTED_YEAR} 年湖北初中数学教资笔试的两次考期。"
                f"请按报考地和当期公告核实；[湖北省教育考试院教师资格栏目]({_OFFICIAL_NOTICES})。"
                if query.outside_coverage else query.clarification()
            )
            return ToolResult(success=True, data={
                "answer": answer, "scope": scope, "matched": False,
            })

        notice = _NOTICES[query.session]
        registration = f"[湖北省教育考试院 · {notice.label}报名通告]({notice.url})"
        subjects = f"[中国教育考试网 · 初中类别笔试科目]({_SUBJECTS})"
        syllabus = f"[中国教育考试网 · 304 数学学科大纲]({_SYLLABUS})"
        answer = (
            f"### {SUPPORTED_YEAR} 年{notice.label} · 湖北 · 初中数学教资笔试\n\n"
            f"- **时间**　网上报名 {notice.registration}；缴费截至 {notice.payment}；"
            f"准考证打印 {notice.admission_ticket}；笔试 {notice.exam_date}。{registration}\n"
            "- **报名核对**　初级中学类别的学历要求为本科毕业及以上。"
            "按公告确认适用的在校学籍、户籍或有效居住证要求，并去所选考区核对审核安排。"
            f"需备电子证件照，具体提交内容以考区要求为准。{registration}\n"
            "- **考试科目**　综合素质（中学）、教育知识与能力、数学学科知识与教学能力（初级中学）。"
            f"{subjects}\n"
            "- **备考顺序**　先按 304 大纲列知识点，再分别安排数学内容、教学设计和公共科目练习。"
            f"{syllabus}\n\n"
            f"以上时间是该考期的公告记录。办理报名和查看新公告请进入[中国教育考试网]({_EXAM_PORTAL})。"
        )
        return ToolResult(success=True, data={
            "answer": answer, "scope": scope, "matched": True,
            "sources": [notice.url, _SUBJECTS, _SYLLABUS],
        })
