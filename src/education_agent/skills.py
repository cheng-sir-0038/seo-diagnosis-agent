from __future__ import annotations

from enum import StrEnum

from src.agent.config import Skill, SkillRegistry
from src.agent.tools.registry import ToolRegistry
from src.education_agent.practice import MAX_QUESTIONS, MathPractice, update_review_ids
from src.education_agent.resources import LearningResources
from src.education_agent.teacher_exam import TeacherWrittenExam

LEARNING_DOMAIN = "learning"
EXAM_PREP_DOMAIN = "exam_prep"


class EducationSkill(StrEnum):
    MATH_PRACTICE = "math-practice"
    RESOURCES = "math-resources"
    TEACHER_EXAM = "teacher-written-exam"


EDUCATION_SKILLS = SkillRegistry([
    Skill(
        name=EducationSkill.MATH_PRACTICE, domain=LEARNING_DOMAIN, description="一元一次方程练习",
        tools=[MathPractice.name],
        prompt_fragment=(
            "想做题、刷题或重练错题时，learning_skill 选 math-practice，填写 math_practice。"
            "保留用户明确要求的数学主题和题数，没有要求时省略。"
            "只在用户要求重练错题时设 review=true。"
            "题库现有一元一次方程及应用题。其他题型也保留用户原主题，不替用户改题型。"
        ),
    ),
    Skill(
        name=EducationSkill.RESOURCES, domain=LEARNING_DOMAIN, description="官方数学学习资料",
        tools=[LearningResources.name],
        prompt_fragment=(
            "想找数学课程、练习链接或学习资料时，learning_skill 选 math-resources，填写 resources。"
            "仅提取用户明确给出的主题和年龄。用户明确说完成了练习时设 completed=true，"
            "并将其提及的练习主题或平台名写入 topic。"
        ),
    ),
    Skill(
        name=EducationSkill.TEACHER_EXAM, domain=EXAM_PREP_DOMAIN, description="教师资格笔试核对",
        tools=[TeacherWrittenExam.name],
        prompt_fragment=(
            "教师资格笔试的报名、科目、大纲或备考选 exam_prep，填写 teacher_exam。"
            "region 为报考省份，武汉等明确属于湖北的考区用湖北。"
            "session 上半年为 first，下半年为 second。"
            "subject 写学段与学科，例如初中数学。仅提取已确认条件，其余留空。"
            "后续追问可沿用同一考试的已确认条件。保留用户新指定的地区、年份、学段和学科。"
            "教资面试、教师招聘和其他考试不使用这个工具。"
        ),
    ),
])


def education_tools(
    domain: str, *, review_ids: list[str] | None = None,
    completed_resources: list[str] | None = None, practice_limit: int = MAX_QUESTIONS,
) -> ToolRegistry:
    if domain == LEARNING_DOMAIN:
        return ToolRegistry([
            MathPractice(review_ids, limit=practice_limit), LearningResources(completed_resources),
        ])
    if domain == EXAM_PREP_DOMAIN:
        return ToolRegistry([TeacherWrittenExam()])
    raise KeyError(domain)


def learning_history(receipts: list[dict]) -> dict[str, list[str]]:
    review_ids: list[str] = []
    completed_resources: set[str] = set()
    for data in receipts:
        if data.get("counts"):
            review_ids = update_review_ids(review_ids, data["progress"])
        if resource_id := data.get("completed_resource_id"):
            completed_resources.add(resource_id)
    return {"review_ids": review_ids, "completed_resources": sorted(completed_resources)}
