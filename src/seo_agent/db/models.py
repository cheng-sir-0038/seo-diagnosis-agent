"""PostgreSQL mappings for the SEO business and conversation tables."""

from datetime import UTC, datetime
from typing import Annotated, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    Table,
    Text,
    UniqueConstraint,
    and_,
    column,
    event,
    or_,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {str: Text, int: BigInteger, float: Double}

    def as_dict(self) -> dict[str, Any]:
        return {col.key: getattr(self, col.key) for col in self.__table__.columns}


TextId = Annotated[str, mapped_column(primary_key=True)]


class SchemaMigrationRow(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    applied_at: Mapped[str]


@event.listens_for(SchemaMigrationRow.__table__, "after_create")
def _record_initial_version(table: Table, connection: Connection, **_: Any) -> None:
    connection.execute(table.insert().values(version=1, applied_at=datetime.now(UTC).isoformat()))


class SiteRow(Base):
    __tablename__ = "sites"

    id: Mapped[TextId]
    name: Mapped[str]
    domain: Mapped[str] = mapped_column(unique=True)
    default_country: Mapped[str]
    default_language: Mapped[str]
    timezone: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="ACTIVE")
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class ActorRow(Base):
    __tablename__ = "actors"

    id: Mapped[TextId]
    actor_type: Mapped[str]
    name: Mapped[str]
    email: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="ACTIVE")
    external_reference: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class AdapterDefinitionRow(Base):
    __tablename__ = "adapter_definitions"
    __table_args__ = (UniqueConstraint("category", "adapter_key"),)

    id: Mapped[TextId]
    category: Mapped[str]
    adapter_key: Mapped[str]
    display_name: Mapped[str]
    implementation_status: Mapped[str]
    capabilities_json: Mapped[str] = mapped_column(server_default="[]")
    config_schema_json: Mapped[str] = mapped_column(server_default="{}")
    created_at: Mapped[str]
    updated_at: Mapped[str]


class SiteAdapterBindingRow(Base):
    __tablename__ = "site_adapter_bindings"
    __table_args__ = (
        CheckConstraint(column("enabled").in_((0, 1))),
        CheckConstraint(column("is_primary").in_((0, 1))),
        Index("idx_site_bindings_site", "site_id"),
        UniqueConstraint("site_id", "adapter_definition_id"),
    )

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    adapter_definition_id: Mapped[str] = mapped_column(
        ForeignKey("adapter_definitions.id", ondelete="RESTRICT")
    )
    enabled: Mapped[int] = mapped_column(server_default="1")
    is_primary: Mapped[int] = mapped_column(server_default="0")
    config_json: Mapped[str] = mapped_column(server_default="{}")
    secret_reference: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class WorkScheduleRow(Base):
    __tablename__ = "work_schedules"
    __table_args__ = (
        CheckConstraint(column("enabled").in_((0, 1))),
        Index("idx_schedules_site", "site_id", "schedule_type", "enabled"),
        Index(
            "ux_active_opportunity_schedule",
            "site_id",
            unique=True,
            postgresql_where=and_(
                column("schedule_type") == "OPPORTUNITY_SCAN",
                column("enabled") == 1,
                column("archived_at").is_(None),
            ),
        ),
    )

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    schedule_type: Mapped[str]
    name: Mapped[str]
    frequency: Mapped[str]
    weekday: Mapped[int | None]
    execution_time: Mapped[str | None]
    timezone: Mapped[str]
    enabled: Mapped[int] = mapped_column(server_default="1")
    next_run_at: Mapped[str | None]
    last_run_at: Mapped[str | None]
    config_json: Mapped[str] = mapped_column(server_default="{}")
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class ScheduleRunRow(Base):
    __tablename__ = "schedule_runs"
    __table_args__ = (
        Index("idx_schedule_runs_schedule", "schedule_id", "scheduled_at"),
        Index(
            "ux_running_schedule_run",
            "schedule_id",
            unique=True,
            postgresql_where=column("status") == "RUNNING",
        ),
    )

    id: Mapped[TextId]
    schedule_id: Mapped[str] = mapped_column(ForeignKey("work_schedules.id", ondelete="RESTRICT"))
    trigger_type: Mapped[str]
    scheduled_at: Mapped[str | None]
    started_at: Mapped[str | None]
    completed_at: Mapped[str | None]
    status: Mapped[str]
    skip_reason: Mapped[str | None]
    existing_run_id: Mapped[str | None]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[str]


class WorkflowThreadRow(Base):
    __tablename__ = "workflow_threads"
    __table_args__ = (
        CheckConstraint(
            or_(column("site_id").is_not(None), column("workflow_type") == "AGENT_CHAT")
        ),
    )

    id: Mapped[TextId]
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    thread_id: Mapped[str] = mapped_column(unique=True)
    workflow_type: Mapped[str]
    graph_version: Mapped[str]
    policy_version: Mapped[str]
    business_object_type: Mapped[str]
    business_object_id: Mapped[str]
    status: Mapped[str]
    current_stage: Mapped[str | None]
    current_checkpoint_id: Mapped[str | None]
    created_at: Mapped[str]
    last_resumed_at: Mapped[str | None]
    completed_at: Mapped[str | None]
    archived_at: Mapped[str | None]


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("idx_runs_thread", "workflow_thread_id", "started_at"),)

    id: Mapped[TextId]
    workflow_thread_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_threads.id", ondelete="RESTRICT")
    )
    schedule_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_runs.id", ondelete="RESTRICT")
    )
    trigger_type: Mapped[str]
    status: Mapped[str]
    current_stage: Mapped[str | None]
    model_provider: Mapped[str | None]
    model_name: Mapped[str | None]
    prompt_version: Mapped[str | None]
    token_usage_json: Mapped[str] = mapped_column(server_default="{}")
    started_at: Mapped[str]
    completed_at: Mapped[str | None]
    stop_reason: Mapped[str | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    created_at: Mapped[str]


class AgentRuntimeTaskRow(Base):
    __tablename__ = "agent_runtime_tasks"
    __table_args__ = (
        Index("idx_runtime_tasks_site_status", "site_id", "status_group", "updated_at"),
    )

    id: Mapped[TextId]
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    conversation_id: Mapped[str | None]
    workflow_thread_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_threads.id", ondelete="RESTRICT")
    )
    title: Mapped[str]
    user_intent: Mapped[str]
    status: Mapped[str]
    status_group: Mapped[str]
    current_stage: Mapped[str | None]
    current_run_id: Mapped[str | None]
    survival_summary_json: Mapped[str] = mapped_column(server_default="{}")
    prompt_version: Mapped[str | None]
    prompt_hash: Mapped[str | None]
    available_tools_json: Mapped[str] = mapped_column(server_default="[]")
    error_message: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]
    completed_at: Mapped[str | None]


class AgentRunEventRow(Base):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        Index("idx_agent_run_events_run", "agent_run_id", "sequence"),
        UniqueConstraint("agent_run_id", "sequence"),
    )

    id: Mapped[TextId]
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"))
    sequence: Mapped[int]
    event_type: Mapped[str]
    payload_json: Mapped[str] = mapped_column(server_default="{}")
    created_at: Mapped[str]


class AgentRunCheckpointRow(Base):
    __tablename__ = "agent_run_checkpoints"
    __table_args__ = (
        Index("idx_agent_run_checkpoints_run", "agent_run_id", "checkpoint_no"),
        UniqueConstraint("agent_run_id", "checkpoint_no"),
    )

    id: Mapped[TextId]
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"))
    checkpoint_no: Mapped[int]
    stage: Mapped[str]
    iteration: Mapped[int] = mapped_column(server_default="0")
    tool_count: Mapped[int] = mapped_column(server_default="0")
    messages_json: Mapped[str]
    pending_tool_calls_json: Mapped[str] = mapped_column(server_default="[]")
    token_usage_json: Mapped[str] = mapped_column(server_default="{}")
    survival_summary_json: Mapped[str] = mapped_column(server_default="{}")
    dispatch_state_json: Mapped[str] = mapped_column(server_default="{}")
    created_at: Mapped[str]


class AgentSurvivalSummaryRow(Base):
    __tablename__ = "agent_survival_summaries"

    id: Mapped[TextId]
    agent_runtime_task_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runtime_tasks.id", ondelete="RESTRICT")
    )
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"))
    checkpoint_id: Mapped[str] = mapped_column(
        ForeignKey("agent_run_checkpoints.id", ondelete="RESTRICT")
    )
    summary_json: Mapped[str]
    created_at: Mapped[str]


class KeywordRow(Base):
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("normalized_keyword", "language", "country"),)

    id: Mapped[TextId]
    normalized_keyword: Mapped[str]
    language: Mapped[str]
    country: Mapped[str]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class CompetitorPageRow(Base):
    __tablename__ = "competitor_pages"
    __table_args__ = (UniqueConstraint("site_id", "url"),)

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    competitor_domain: Mapped[str]
    url: Mapped[str]
    title: Mapped[str | None]
    first_seen_at: Mapped[str]
    last_seen_at: Mapped[str]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        Index("idx_opportunity_status", "site_id", "status", "priority_level"),
        UniqueConstraint("site_id", "fingerprint"),
    )

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    fingerprint: Mapped[str]
    normalized_intent: Mapped[str]
    opportunity_type: Mapped[str]
    target_country: Mapped[str]
    target_language: Mapped[str]
    title: Mapped[str]
    current_score: Mapped[int | None]
    priority_level: Mapped[str | None]
    status: Mapped[str]
    current_summary: Mapped[str | None]
    policy_version: Mapped[str]
    first_seen_at: Mapped[str]
    last_seen_at: Mapped[str]
    seen_count: Mapped[int] = mapped_column(server_default="1")
    rejected_at: Mapped[str | None]
    rejection_reason: Mapped[str | None]
    reopened_at: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class OpportunityObservationRow(Base):
    __tablename__ = "opportunity_observations"
    __table_args__ = (Index("idx_observations_opportunity", "opportunity_id", "observed_at"),)

    id: Mapped[TextId]
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id", ondelete="RESTRICT"))
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    score: Mapped[int]
    priority_level: Mapped[str]
    search_volume: Mapped[float | None]
    keyword_difficulty: Mapped[float | None]
    competitor_count: Mapped[int | None]
    gap_summary: Mapped[str | None]
    score_factors_json: Mapped[str] = mapped_column(server_default="{}")
    policy_version: Mapped[str]
    threshold_snapshot_json: Mapped[str] = mapped_column(server_default="{}")
    observed_at: Mapped[str]
    created_at: Mapped[str]


class OpportunityStatusHistoryRow(Base):
    __tablename__ = "opportunity_status_history"

    id: Mapped[TextId]
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id", ondelete="RESTRICT"))
    from_status: Mapped[str | None]
    to_status: Mapped[str]
    reason: Mapped[str | None]
    policy_version: Mapped[str]
    threshold_snapshot_json: Mapped[str] = mapped_column(server_default="{}")
    triggered_conditions_json: Mapped[str] = mapped_column(server_default="[]")
    changed_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    changed_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[str]


class DiagnosisHypothesisRow(Base):
    __tablename__ = "diagnosis_hypotheses"

    id: Mapped[TextId]
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id", ondelete="RESTRICT"))
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    title: Mapped[str]
    description: Mapped[str | None]
    status: Mapped[str]
    confidence: Mapped[str | None]
    conclusion: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class HypothesisEvidenceRow(Base):
    __tablename__ = "hypothesis_evidence"
    __table_args__ = (UniqueConstraint("hypothesis_id", "evidence_snapshot_id", "relation_type"),)

    id: Mapped[TextId]
    hypothesis_id: Mapped[str] = mapped_column(
        ForeignKey("diagnosis_hypotheses.id", ondelete="RESTRICT")
    )
    evidence_snapshot_id: Mapped[str]
    relation_type: Mapped[str]
    relevance_score: Mapped[float | None]
    explanation: Mapped[str | None]
    linked_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[str]


class OpportunityObservationEvidenceRow(Base):
    __tablename__ = "opportunity_observation_evidence"
    __table_args__ = (UniqueConstraint("observation_id", "evidence_snapshot_id", "evidence_role"),)

    id: Mapped[TextId]
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("opportunity_observations.id", ondelete="RESTRICT")
    )
    evidence_snapshot_id: Mapped[str]
    evidence_role: Mapped[str]
    contribution_weight: Mapped[float | None]
    created_at: Mapped[str]


class OpportunityKeywordRow(Base):
    __tablename__ = "opportunity_keywords"
    __table_args__ = (UniqueConstraint("opportunity_id", "keyword_id", "keyword_role"),)

    id: Mapped[TextId]
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id", ondelete="RESTRICT"))
    keyword_id: Mapped[str] = mapped_column(ForeignKey("keywords.id", ondelete="RESTRICT"))
    keyword_role: Mapped[str]
    relevance_score: Mapped[float | None]
    first_seen_at: Mapped[str]
    last_seen_at: Mapped[str]
    created_at: Mapped[str]


class OpportunityCompetitorPageRow(Base):
    __tablename__ = "opportunity_competitor_pages"
    __table_args__ = (UniqueConstraint("opportunity_id", "competitor_page_id", "gap_type"),)

    id: Mapped[TextId]
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id", ondelete="RESTRICT"))
    competitor_page_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_pages.id", ondelete="RESTRICT")
    )
    gap_type: Mapped[str]
    relevance_score: Mapped[float | None]
    evidence_snapshot_id: Mapped[str | None]
    created_at: Mapped[str]


class ApprovalActionRow(Base):
    __tablename__ = "approval_actions"
    __table_args__ = (Index("idx_approval_waiting", "site_id", "status", "next_remind_at"),)

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    action_type: Mapped[str]
    risk_level: Mapped[str]
    status: Mapped[str]
    target_type: Mapped[str]
    target_id: Mapped[str]
    requested_payload_json: Mapped[str] = mapped_column(server_default="{}")
    requested_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    requested_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    requested_at: Mapped[str]
    next_remind_at: Mapped[str | None]
    last_reminded_at: Mapped[str | None]
    decided_at: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class ApprovalActionEvidenceRow(Base):
    __tablename__ = "approval_action_evidence"
    __table_args__ = (
        UniqueConstraint("approval_action_id", "evidence_snapshot_id", "evidence_role"),
    )

    id: Mapped[TextId]
    approval_action_id: Mapped[str] = mapped_column(
        ForeignKey("approval_actions.id", ondelete="RESTRICT")
    )
    evidence_snapshot_id: Mapped[str]
    evidence_role: Mapped[str]
    display_order: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[str]


class ApprovalActionApproverRow(Base):
    __tablename__ = "approval_action_approvers"
    __table_args__ = (
        CheckConstraint(column("is_required").in_((0, 1))),
        UniqueConstraint("approval_action_id", "actor_id"),
    )

    id: Mapped[TextId]
    approval_action_id: Mapped[str] = mapped_column(
        ForeignKey("approval_actions.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id", ondelete="RESTRICT"))
    approval_order: Mapped[int] = mapped_column(server_default="1")
    is_required: Mapped[int] = mapped_column(server_default="1")
    status: Mapped[str]
    notified_at: Mapped[str | None]
    decided_at: Mapped[str | None]
    created_at: Mapped[str]


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[TextId]
    approval_action_approver_id: Mapped[str] = mapped_column(
        ForeignKey("approval_action_approvers.id", ondelete="RESTRICT")
    )
    decision: Mapped[str]
    original_payload_json: Mapped[str] = mapped_column(server_default="{}")
    edited_payload_json: Mapped[str | None]
    reason: Mapped[str | None]
    decided_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[str]
    created_at: Mapped[str]


class ExecutionTaskRow(Base):
    __tablename__ = "execution_tasks"
    __table_args__ = (Index("idx_tasks_status", "site_id", "status", "deadline_at"),)

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    title: Mapped[str]
    task_type: Mapped[str]
    status: Mapped[str]
    priority: Mapped[str]
    current_prd_version_id: Mapped[str | None]
    deadline_at: Mapped[str | None]
    created_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class TaskOpportunityRow(Base):
    __tablename__ = "task_opportunities"
    __table_args__ = (
        CheckConstraint(column("is_primary").in_((0, 1))),
        UniqueConstraint("task_id", "opportunity_id"),
    )

    id: Mapped[TextId]
    task_id: Mapped[str] = mapped_column(ForeignKey("execution_tasks.id", ondelete="RESTRICT"))
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id", ondelete="RESTRICT"))
    relation_type: Mapped[str]
    is_primary: Mapped[int] = mapped_column(server_default="0")
    contribution_weight: Mapped[float | None]
    linked_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[str]


class TaskAssignmentRow(Base):
    __tablename__ = "task_assignments"
    __table_args__ = (
        UniqueConstraint("task_id", "actor_id", "assignment_role"),
        Index(
            "ux_active_task_owner",
            "task_id",
            unique=True,
            postgresql_where=and_(
                column("assignment_role") == "OWNER",
                column("status").in_(("ASSIGNED", "ACCEPTED")),
            ),
        ),
    )

    id: Mapped[TextId]
    task_id: Mapped[str] = mapped_column(ForeignKey("execution_tasks.id", ondelete="RESTRICT"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id", ondelete="RESTRICT"))
    assignment_role: Mapped[str]
    status: Mapped[str]
    assigned_at: Mapped[str]
    accepted_at: Mapped[str | None]
    completed_at: Mapped[str | None]
    created_at: Mapped[str]


class TaskStatusHistoryRow(Base):
    __tablename__ = "task_status_history"

    id: Mapped[TextId]
    task_id: Mapped[str] = mapped_column(ForeignKey("execution_tasks.id", ondelete="RESTRICT"))
    from_status: Mapped[str | None]
    to_status: Mapped[str]
    reason: Mapped[str | None]
    changed_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    changed_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[str]


class TaskPrdVersionRow(Base):
    __tablename__ = "task_prd_versions"
    __table_args__ = (UniqueConstraint("task_id", "version_no"),)

    id: Mapped[TextId]
    task_id: Mapped[str] = mapped_column(ForeignKey("execution_tasks.id", ondelete="RESTRICT"))
    version_no: Mapped[int]
    status: Mapped[str]
    title: Mapped[str]
    structured_content_json: Mapped[str]
    markdown_content: Mapped[str]
    prompt_version_id: Mapped[str | None]
    source_trace_id: Mapped[str | None]
    generated_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    source_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_prd_versions.id", ondelete="RESTRICT")
    )
    change_summary: Mapped[str | None]
    created_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    created_at: Mapped[str]


class TaskPrdEvidenceRow(Base):
    __tablename__ = "task_prd_evidence"
    __table_args__ = (
        UniqueConstraint("prd_version_id", "evidence_snapshot_id", "evidence_role", "section_key"),
    )

    id: Mapped[TextId]
    prd_version_id: Mapped[str] = mapped_column(
        ForeignKey("task_prd_versions.id", ondelete="RESTRICT")
    )
    evidence_snapshot_id: Mapped[str]
    evidence_role: Mapped[str]
    section_key: Mapped[str]
    citation_label: Mapped[str | None]
    created_at: Mapped[str]


class TaskPrdArtifactRow(Base):
    __tablename__ = "task_prd_artifacts"
    __table_args__ = (UniqueConstraint("prd_version_id", "renderer_key", "checksum"),)

    id: Mapped[TextId]
    prd_version_id: Mapped[str] = mapped_column(
        ForeignKey("task_prd_versions.id", ondelete="RESTRICT")
    )
    renderer_key: Mapped[str]
    file_name: Mapped[str]
    file_path: Mapped[str]
    content_type: Mapped[str]
    checksum: Mapped[str]
    generated_at: Mapped[str]
    created_at: Mapped[str]


class TaskDispatchRow(Base):
    __tablename__ = "task_dispatches"

    id: Mapped[TextId]
    task_id: Mapped[str] = mapped_column(ForeignKey("execution_tasks.id", ondelete="RESTRICT"))
    prd_version_id: Mapped[str] = mapped_column(
        ForeignKey("task_prd_versions.id", ondelete="RESTRICT")
    )
    dispatcher_adapter: Mapped[str]
    recipient_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    status: Mapped[str]
    external_reference: Mapped[str | None]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    dispatched_at: Mapped[str | None]
    accepted_at: Mapped[str | None]
    failed_at: Mapped[str | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    created_at: Mapped[str]


class DeliveryRow(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("task_id", "version_no"),)

    id: Mapped[TextId]
    task_id: Mapped[str] = mapped_column(ForeignKey("execution_tasks.id", ondelete="RESTRICT"))
    prd_version_id: Mapped[str] = mapped_column(
        ForeignKey("task_prd_versions.id", ondelete="RESTRICT")
    )
    version_no: Mapped[int]
    parent_delivery_id: Mapped[str | None] = mapped_column(
        ForeignKey("deliveries.id", ondelete="RESTRICT")
    )
    status: Mapped[str]
    title: Mapped[str]
    summary: Mapped[str | None]
    structured_content_json: Mapped[str] = mapped_column(server_default="{}")
    submitted_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    submitted_at: Mapped[str]
    created_at: Mapped[str]


class DeliveryArtifactRow(Base):
    __tablename__ = "delivery_artifacts"

    id: Mapped[TextId]
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id", ondelete="RESTRICT"))
    artifact_type: Mapped[str]
    file_name: Mapped[str | None]
    file_path: Mapped[str | None]
    content_url: Mapped[str | None]
    content_type: Mapped[str | None]
    checksum: Mapped[str | None]
    metadata_json: Mapped[str] = mapped_column(server_default="{}")
    created_at: Mapped[str]


class DeliveryReviewRow(Base):
    __tablename__ = "delivery_reviews"

    id: Mapped[TextId]
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id", ondelete="RESTRICT"))
    prd_version_id: Mapped[str] = mapped_column(
        ForeignKey("task_prd_versions.id", ondelete="RESTRICT")
    )
    reviewer_type: Mapped[str]
    reviewer_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    status: Mapped[str]
    score: Mapped[int | None]
    passed_items_json: Mapped[str] = mapped_column(server_default="[]")
    failed_items_json: Mapped[str] = mapped_column(server_default="[]")
    missing_items_json: Mapped[str] = mapped_column(server_default="[]")
    revision_instructions_json: Mapped[str] = mapped_column(server_default="[]")
    prompt_version_id: Mapped[str | None]
    source_trace_id: Mapped[str | None]
    reviewed_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[str]
    created_at: Mapped[str]


class DeliveryReviewEvidenceRow(Base):
    __tablename__ = "delivery_review_evidence"
    __table_args__ = (
        UniqueConstraint(
            "delivery_review_id", "evidence_snapshot_id", "evidence_role", "review_item_key"
        ),
    )

    id: Mapped[TextId]
    delivery_review_id: Mapped[str] = mapped_column(
        ForeignKey("delivery_reviews.id", ondelete="RESTRICT")
    )
    evidence_snapshot_id: Mapped[str]
    evidence_role: Mapped[str]
    review_item_key: Mapped[str]
    created_at: Mapped[str]


class PublishRecordRow(Base):
    __tablename__ = "publish_records"

    id: Mapped[TextId]
    task_id: Mapped[str] = mapped_column(ForeignKey("execution_tasks.id", ondelete="RESTRICT"))
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id", ondelete="RESTRICT"))
    publisher_adapter: Mapped[str]
    status: Mapped[str]
    target_environment: Mapped[str]
    candidate_url: Mapped[str | None]
    published_url: Mapped[str | None]
    published_at: Mapped[str | None]
    submitted_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    confirmed_by_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    confirmed_at: Mapped[str | None]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    approval_action_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_actions.id", ondelete="RESTRICT")
    )
    source_trace_id: Mapped[str | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class MonitoringPlanRow(Base):
    __tablename__ = "monitoring_plans"

    id: Mapped[TextId]
    publish_record_id: Mapped[str] = mapped_column(
        ForeignKey("publish_records.id", ondelete="RESTRICT")
    )
    status: Mapped[str]
    policy_version: Mapped[str]
    origin_trace_id: Mapped[str | None]
    indexing_started_at: Mapped[str]
    indexed_at: Mapped[str | None]
    ranking_started_at: Mapped[str | None]
    ranking_ends_at: Mapped[str | None]
    next_check_at: Mapped[str | None]
    final_outcome: Mapped[str | None]
    completed_at: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class MonitoringGoalRow(Base):
    __tablename__ = "monitoring_goals"

    id: Mapped[TextId]
    monitoring_plan_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_plans.id", ondelete="RESTRICT")
    )
    goal_type: Mapped[str]
    target_keyword: Mapped[str | None]
    target_value: Mapped[float | None]
    comparison_operator: Mapped[str | None]
    minimum_improvement: Mapped[float | None]
    baseline_value: Mapped[float | None]
    created_at: Mapped[str]


class MonitoringCheckRow(Base):
    __tablename__ = "monitoring_checks"
    __table_args__ = (Index("idx_monitoring_checks_due", "status", "scheduled_for"),)

    id: Mapped[TextId]
    monitoring_plan_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_plans.id", ondelete="RESTRICT")
    )
    check_type: Mapped[str]
    sequence_no: Mapped[int]
    scheduled_for: Mapped[str]
    started_at: Mapped[str | None]
    completed_at: Mapped[str | None]
    status: Mapped[str]
    conclusion: Mapped[str | None]
    trace_id: Mapped[str | None]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    error_code: Mapped[str | None]
    created_at: Mapped[str]


class MonitoringObservationRow(Base):
    __tablename__ = "monitoring_observations"

    id: Mapped[TextId]
    monitoring_check_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_checks.id", ondelete="RESTRICT")
    )
    metric_type: Mapped[str]
    keyword_id: Mapped[str | None] = mapped_column(ForeignKey("keywords.id", ondelete="RESTRICT"))
    value_numeric: Mapped[float | None]
    value_text: Mapped[str | None]
    previous_value_numeric: Mapped[float | None]
    change_value: Mapped[float | None]
    evidence_snapshot_id: Mapped[str | None]
    observed_at: Mapped[str]
    created_at: Mapped[str]


class MonitoringOutcomeRow(Base):
    __tablename__ = "monitoring_outcomes"

    id: Mapped[TextId]
    monitoring_plan_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_plans.id", ondelete="RESTRICT")
    )
    outcome: Mapped[str]
    confidence: Mapped[str]
    summary: Mapped[str]
    evidence_summary_json: Mapped[str] = mapped_column(server_default="{}")
    decided_by_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[str]


class MonitoringOutcomeGoalRow(Base):
    __tablename__ = "monitoring_outcome_goals"
    __table_args__ = (UniqueConstraint("monitoring_outcome_id", "monitoring_goal_id"),)

    id: Mapped[TextId]
    monitoring_outcome_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_outcomes.id", ondelete="RESTRICT")
    )
    monitoring_goal_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_goals.id", ondelete="RESTRICT")
    )
    status: Mapped[str]
    actual_value: Mapped[float | None]
    target_value_snapshot: Mapped[float | None]
    improvement_percent: Mapped[float | None]
    explanation: Mapped[str | None]
    created_at: Mapped[str]


class NotificationSubscriptionRow(Base):
    __tablename__ = "notification_subscriptions"
    __table_args__ = (
        CheckConstraint(column("enabled").in_((0, 1))),
        CheckConstraint(column("is_default_approver").in_((0, 1))),
    )

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    subscriber_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT")
    )
    recipient_email: Mapped[str]
    recipient_role: Mapped[str]
    channel: Mapped[str]
    enabled: Mapped[int] = mapped_column(server_default="1")
    is_default_approver: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class NotificationEventTypeRow(Base):
    __tablename__ = "notification_event_types"
    __table_args__ = (
        CheckConstraint(column("supports_schedule").in_((0, 1))),
        CheckConstraint(column("supports_threshold").in_((0, 1))),
    )

    id: Mapped[TextId]
    event_key: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    description: Mapped[str | None]
    default_delivery_mode: Mapped[str]
    supports_threshold: Mapped[int] = mapped_column(server_default="0")
    supports_schedule: Mapped[int] = mapped_column(server_default="0")
    status: Mapped[str]
    created_at: Mapped[str]


class NotificationPreferenceRow(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint(column("enabled").in_((0, 1))),
        CheckConstraint(column("mandatory").in_((0, 1))),
        UniqueConstraint("subscription_id", "event_type_id"),
    )

    id: Mapped[TextId]
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("notification_subscriptions.id", ondelete="RESTRICT")
    )
    event_type_id: Mapped[str] = mapped_column(
        ForeignKey("notification_event_types.id", ondelete="RESTRICT")
    )
    enabled: Mapped[int] = mapped_column(server_default="1")
    mandatory: Mapped[int] = mapped_column(server_default="0")
    delivery_mode: Mapped[str]
    schedule_json: Mapped[str] = mapped_column(server_default="{}")
    threshold_json: Mapped[str] = mapped_column(server_default="{}")
    created_at: Mapped[str]
    updated_at: Mapped[str]


class NotificationEventRow(Base):
    __tablename__ = "notification_events"

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    event_type_id: Mapped[str] = mapped_column(
        ForeignKey("notification_event_types.id", ondelete="RESTRICT")
    )
    business_object_type: Mapped[str]
    business_object_id: Mapped[str]
    title: Mapped[str]
    content_json: Mapped[str]
    priority: Mapped[str]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[str]


class NotificationDeliveryRow(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("idx_notification_due", "status", "scheduled_at"),)

    id: Mapped[TextId]
    notification_event_id: Mapped[str] = mapped_column(
        ForeignKey("notification_events.id", ondelete="RESTRICT")
    )
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("notification_subscriptions.id", ondelete="RESTRICT")
    )
    channel_adapter: Mapped[str]
    provider_adapter: Mapped[str]
    recipient: Mapped[str]
    status: Mapped[str]
    provider_message_id: Mapped[str | None]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    scheduled_at: Mapped[str | None]
    sent_at: Mapped[str | None]
    failed_at: Mapped[str | None]
    created_at: Mapped[str]


class NotificationAttemptRow(Base):
    __tablename__ = "notification_attempts"
    __table_args__ = (UniqueConstraint("notification_delivery_id", "attempt_no"),)

    id: Mapped[TextId]
    notification_delivery_id: Mapped[str] = mapped_column(
        ForeignKey("notification_deliveries.id", ondelete="RESTRICT")
    )
    attempt_no: Mapped[int]
    provider: Mapped[str]
    status: Mapped[str]
    response_code: Mapped[str | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    started_at: Mapped[str]
    completed_at: Mapped[str | None]
    created_at: Mapped[str]


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    title: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[str]
    updated_at: Mapped[str]
    archived_at: Mapped[str | None]


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[TextId]
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT")
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    role: Mapped[str]
    content: Mapped[str]
    tool_call_id: Mapped[str | None]
    metadata_json: Mapped[str] = mapped_column(server_default="{}")
    created_at: Mapped[str]


class ConversationRelationRow(Base):
    __tablename__ = "conversation_relations"
    __table_args__ = (
        UniqueConstraint("conversation_id", "object_type", "object_id", "relation_type"),
    )

    id: Mapped[TextId]
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT")
    )
    object_type: Mapped[str]
    object_id: Mapped[str]
    relation_type: Mapped[str]
    created_at: Mapped[str]


class WorkRecordRow(Base):
    __tablename__ = "work_records"
    __table_args__ = (Index("idx_work_records_site", "site_id", "occurred_at"),)

    id: Mapped[TextId]
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    workflow_thread_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_threads.id", ondelete="RESTRICT")
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    record_type: Mapped[str]
    title: Mapped[str]
    summary: Mapped[str]
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("actors.id", ondelete="RESTRICT"))
    payload_json: Mapped[str] = mapped_column(server_default="{}")
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    occurred_at: Mapped[str]
    created_at: Mapped[str]


class WorkRecordRelationRow(Base):
    __tablename__ = "work_record_relations"
    __table_args__ = (
        UniqueConstraint("work_record_id", "object_type", "object_id", "relation_role"),
    )

    id: Mapped[TextId]
    work_record_id: Mapped[str] = mapped_column(ForeignKey("work_records.id", ondelete="RESTRICT"))
    object_type: Mapped[str]
    object_id: Mapped[str]
    relation_role: Mapped[str]
    created_at: Mapped[str]


TABLES = Base.metadata.tables
ROWS = {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers}
