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
            "保留用户明确要求的数学主题、题数和难度，没有要求时省略。"
            "difficulty 基础或简单为 basic，中等为 intermediate，进阶或较难为 advanced。"
            "只在用户要求重练错题时设 review=true。"
            "不要求用户提供年龄、题源或自拟题目；不提前填答案。"
            "当前题库按 TAL-SCQ5K 原题逐道出一元一次方程及应用题、按原答案判分、作答后展示解析。"
            "工具按题量与本轮预算限制实际题数。重练仅限同对话错题；其他题型或找不到该难度时保留原条件，"
            "让工具说明覆盖缺口，不改题型、降难度或生成替代题。"
        ),
    ),
    Skill(
        name=EducationSkill.RESOURCES, domain=LEARNING_DOMAIN, description="官方数学学习资料",
        tools=[LearningResources.name],
        prompt_fragment=(
            "想找数学课程、练习链接或学习资料时，learning_skill 选 math-resources，填写 resources。"
            "仅提取用户明确给出的主题和年龄。用户明确说完成了练习时设 completed=true，"
            "并将其提及的练习主题或平台名写入 topic。普通推荐中主题和年龄都是筛选项，"
            "未提供年龄时不猜测或追问；不能以‘想做’代替‘已完成’。"
            "当前资料来自 Oxford Owl 分数、小数、百分数活动和 Cambridge NRICH 方程探究，"
            "工具返回匹配的官方链接、适龄范围和可做的动作；无匹配时保留原要求，不虚构资料。"
            "完成记录只绑定匹配到的单项活动，不能从泛称推断完成了多个练习。"
        ),
    ),
    Skill(
        name=EducationSkill.TEACHER_EXAM, domain=EXAM_PREP_DOMAIN, description="教师资格笔试核对",
        tools=[TeacherWrittenExam.name],
        prompt_fragment=(
            "教师资格笔试的报名、科目、大纲或备考选 exam_prep，填写 teacher_exam。"
            "region 为报考省份，武汉等明确属于湖北的考区用湖北。"
            "session 上半年为 first，下半年为 second。"
            "subject 写完整学段与学科，例如初中数学。准确核对需要报考省份、年份、考期、学段学科；"
            "不猜年份与考期，不从参考链接推断报考范围。已确认的条件可续用；明确更正时只更新对应字段。"
            "现有证据覆盖 2026 年湖北初中数学教资笔试的上、下半年。范围外也保留用户的真实范围，"
            "由工具说明证据缺口，不替换成已收录考期或其他省份。"
            "结果来自当期报名公告、官方科目和大纲入口；备考动作属于练习建议，不能冒充公告条款。"
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
