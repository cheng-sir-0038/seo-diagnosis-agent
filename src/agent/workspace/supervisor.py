"""Compatibility imports for the workspace team implementation."""

from src.agent.workspace.team import GraphState, WorkspaceTeam, conversation_context
from src.seo_agent.specialist import SeoSpecialistFactory as SeoSpecialist

WorkspaceSupervisor = WorkspaceTeam

__all__ = ["GraphState", "SeoSpecialist", "WorkspaceSupervisor", "conversation_context"]
