"""通用 Agent 运行时。"""

from src.agent.config import (
    AgentConfig,
    AgentPromptRenderer,
    RenderedAgentPrompt,
    Skill,
    SkillRegistry,
)
from src.agent.run_manager import RunManager, run_manager
from src.agent.runtime.executor import AgentExecutor

# Compatibility for callers importing the former public class from src.agent.
AgentLoop = AgentExecutor

__all__ = [
    "AgentConfig",
    "AgentExecutor",
    "AgentLoop",
    "AgentPromptRenderer",
    "RenderedAgentPrompt",
    "RunManager",
    "Skill",
    "SkillRegistry",
    "run_manager",
]
