from src.agent.tools.registry import ToolRegistry
from src.healthcare_agent.exams import (
    MedicalExamRegistration,
    MedicalExamSyllabus,
    public_rule_evidence,
)
from src.healthcare_agent.medicare import MedicareDrugCatalogue
from src.healthcare_agent.postgraduate import PostgraduateAdmissionsSearch
from src.healthcare_agent.pubmed import PubMedLiteratureSearch
from src.seo_agent.db.database import SeoAgentDatabase

__all__ = ["build_healthcare_registry", "public_rule_evidence"]


def build_healthcare_registry(*, database: SeoAgentDatabase | None = None) -> ToolRegistry:
    registry = ToolRegistry([
        MedicalExamRegistration(), MedicalExamSyllabus(),
        MedicareDrugCatalogue(), PubMedLiteratureSearch(),
    ])
    if database is not None and database.started:
        registry.register(PostgraduateAdmissionsSearch(database))
    return registry
