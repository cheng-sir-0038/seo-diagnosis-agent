"""Compose the workspace application by enrolling each specialist once."""

from src.agent.workspace.registry import COURSE_REVIEW, SpecialistRegistry
from src.education_agent.specialist import EXAM_PREP_SPECIALIST, LEARNING_SPECIALIST
from src.followup_agent.specialist import FOLLOWUP_SPECIALIST
from src.healthcare_agent.specialist import HEALTHCARE_SPECIALIST
from src.procurement_agent.specialist import PROCUREMENT_SPECIALIST
from src.seo_agent.specialist import SEO_SPECIALIST
from src.travel_agent.specialist import TRAVEL_SPECIALIST

workspace_specialists = SpecialistRegistry(
    (
        HEALTHCARE_SPECIALIST,
        SEO_SPECIALIST,
        LEARNING_SPECIALIST,
        EXAM_PREP_SPECIALIST,
        FOLLOWUP_SPECIALIST,
        TRAVEL_SPECIALIST,
        PROCUREMENT_SPECIALIST,
    ),
    handoffs={COURSE_REVIEW: (HEALTHCARE_SPECIALIST.id, SEO_SPECIALIST.id)},
)
