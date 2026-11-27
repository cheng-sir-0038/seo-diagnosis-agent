"""A test-only specialist that needs no custom workflow or frontend code."""

from src.agent.config import Skill
from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.registry import PublicSkill, SpecialistDefinition, SpecialistProfile, ToolContext


class ArchiveLookup(AgentTool):
    name = "archive_lookup"
    description = "核对合成档案编号"
    parameters_schema = {
        "type": "object", "properties": {"archive_id": {"type": "string"}},
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict, runtime: ToolRuntimeContext | None = None) -> ToolResult:
        metadata = runtime.metadata if runtime else {}
        answer = metadata.get("human_input_resume", {}).get("answer")
        archive_id = answer or arguments.get("archive_id")
        if not archive_id:
            return ToolResult(success=True, metadata={"human_input": {"questions": ["请给出档案编号。"]}})
        return ToolResult(success=True, data={"archive_id": archive_id, "source": "合成档案库"})


async def archive_tools(_context: ToolContext) -> ToolRegistry:
    return ToolRegistry([ArchiveLookup()])


ARCHIVE_SPECIALIST = SpecialistDefinition(
    id="archive_review",
    profile=SpecialistProfile("档案核验", "book"),
    routing_prompt="用户请求核对合成档案编号时使用。",
    tools=archive_tools,
    instructions="仅使用本次启用的档案查询工具，依据回执报告档案编号。",
    public_skills=(PublicSkill.publish(
        Skill(name="archive-record", domain="archive_review", description="档案编号核对",
              tools=[ArchiveLookup.name], prompt_fragment="内部运行条件"),
        summary="核对用户指定的合成档案。",
        instruction="按编号读取合成档案，标明档案来源。",
        usage={ArchiveLookup.name: ("核对档案", "档案编号", "只读查询", "编号与来源")},
    ),),
)
