from copy import deepcopy

import pytest

from src.agent.models import AgentRun, RunStatus
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.base import ToolRuntimeContext
from src.healthcare_agent import build_healthcare_registry, public_rule_evidence


@pytest.fixture
def tools():
    return build_healthcare_registry()


RETAKE = {
    "topic": "skills_retake", "year": 2026, "region": "湖北", "exam": "医师资格",
    "skills_year": 2025, "skills_passed": True, "comprehensive_passed": False,
}


async def test_doctor_exam_asks_for_national_base_and_resumes(tools):
    tool = tools.get("medical_exam_registration")
    first = await tool.execute(RETAKE)
    assert first.metadata["human_input"]["questions"] == [
        "你在国家实践技能考试基地参加了这次技能考试吗？"
    ]
    assert "exempt" not in first.data
    runtime = ToolRuntimeContext(metadata={"human_input_resume": {
        "tool_name": tool.name, "tool_arguments": RETAKE, "tool_result": first.model_dump(),
        "answer": "是", "action": "answer",
    }})
    verified = await tool.execute(RETAKE, runtime)
    assert verified.data["exempt"] is True
    assert verified.data["registration_required"] is True
    assert verified.data["items"][0]["url"] == "https://www.hbwsrc.cn/news/show-1830.html"
    assert "湖北" in verified.data["items"][0]["title"]
    assert "human_input" not in verified.metadata


@pytest.mark.parametrize("changed,expected", [
    ({"region": "湖南"}, "outside_scope"), ({"year": 2027}, "outside_scope"),
    ({"exam": "护士资格"}, "outside_scope"), ({"national_base": False}, "not_exempt_under_rule"),
    ({"skills_passed": False}, "not_exempt_under_rule"), ({"skills_year": 2024}, "outside_scope"),
])
async def test_exam_checks_scope_before_applying_an_exemption(tools, changed, expected):
    result = await tools.get("medical_exam_registration").execute(
        {**RETAKE, "national_base": True, **changed},
    )
    assert result.data["status"] == expected and result.data.get("exempt") is not True


async def test_handoff_is_scoped_to_verified_public_syllabus_fields(tools):
    tool = tools.get("medical_exam_syllabus")
    current = await tool.execute({})
    proof = public_rule_evidence(tool.name, current)
    assert set(proof) == {"year", "title", "source_url", "applies_to", "quote"}
    assert proof["year"] == 2026 and "临床执业（助理）医师" in proof["applies_to"]
    assert "进行了修订" in proof["quote"]
    assert proof["source_url"] == "https://www1.nmec.org.cn/Pages/ArticleInfo-4-11903.html"
    assert public_rule_evidence(tool.name, current.model_dump()) == proof
    tampered = deepcopy(current.model_dump())
    tampered["data"]["evidence"]["quote"] = "会员名单 M04"
    assert public_rule_evidence(tool.name, tampered) is None
    tampered = deepcopy(current.model_dump())
    tampered["data"]["evidence"]["members"] = ["M04"]
    assert public_rule_evidence(tool.name, tampered) in (None, proof)
    assert public_rule_evidence(tool.name, {"success": False, "data": current.data}) is None
    old = await tool.execute({"year": 2024})
    assert public_rule_evidence(tool.name, old) is None
    nursing = await tool.execute({"category": "护士资格"})
    assert nursing.data["status"] == "outside_scope"


async def test_agent_loop_resumes_the_same_exam_query_with_the_supplied_condition(tools):
    run = AgentRun(id="exam-demo", messages=[{
        "role": "user",
        "content": "2026 年在湖北报考医师资格，去年技能过了，综合没过，还要考技能吗？",
    }])
    name = "medical_exam_registration"
    agent = AgentExecutor(FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(id="retake", name=name, arguments=RETAKE)])],
        [ModelChunk(text="按所述条件可免技能考试，仍须报名和资格审核。")],
    ]), tools)
    await agent.run(run)
    assert run.status == RunStatus.WAITING_FOR_INPUT and run.human_input.tool_name == name
    run.human_input.answer = "是的"
    run.messages.append({"role": "user", "content": "是的"})
    run.status = RunStatus.RECOVERING
    await agent.run(run)
    assert run.status == RunStatus.COMPLETED and run.human_input is None
    events = [e for e in run.events if e.type == "tool.completed"]
    assert [e.payload["name"] for e in events] == [name, name]
    assert events[0].payload["result"]["data"]["status"] == "needs_input"
    final = events[-1].payload["result"]["data"]
    assert final["exempt"] and final["registration_required"]
    assert final["evidence"]["source_url"].startswith("https://www.hbwsrc.cn/")
    assert "国网报名" in final["evidence"]["quote"]


async def test_unconfirmed_exam_condition_stays_unconfirmed(tools):
    tool = tools.get("medical_exam_registration")
    first = await tool.execute(RETAKE)
    runtime = ToolRuntimeContext(metadata={"human_input_resume": {
        "tool_name": tool.name, "tool_arguments": RETAKE, "tool_result": first.model_dump(),
        "answer": "不确定",
    }})
    result = await tool.execute(RETAKE, runtime)
    assert result.data["status"] == "unconfirmed" and "exempt" not in result.data
    assert "human_input" not in result.metadata


async def test_unknown_from_model_triggers_a_real_question(tools):
    result = await tools.get("medical_exam_registration").execute(
        {**RETAKE, "national_base": "unknown"},
    )
    assert result.data["status"] == "needs_input"
    assert result.metadata["human_input"]["questions"] == [
        "你在国家实践技能考试基地参加了这次技能考试吗？"
    ]


async def test_registration_schedule_matches_the_requested_edition(tools):
    result = await tools.get("medical_exam_registration").execute(
        {"year": 2026, "region": "武汉", "exam": "医师资格考试"},
    )
    assert result.data["status"] == "matched"
    assert "1 月 29 日 10 时至 2 月 11 日 24 时" in result.data["description"]["fallback"]
    assert result.data["evidence"]["section"] == "一、考试报名"
