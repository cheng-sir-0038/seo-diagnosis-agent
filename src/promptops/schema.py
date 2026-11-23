"""Prompt registry, rollout and evaluation records in PostgreSQL."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.observability.schema import Base

PROMPTOPS_SCHEMA_VERSION = "1"


def now() -> datetime:
    return datetime.now(UTC)


def record(row: Base) -> dict[str, Any]:
    """Keep the existing API's ISO timestamps and serialized JSON fields."""
    result = {}
    for name in row.__table__.columns.keys():
        value = getattr(row, name)
        if isinstance(value, datetime):
            value = value.astimezone(UTC).isoformat()
        elif name.endswith("_json"):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result[name] = value
    return result


class PromptSchemaVersion(Base):
    __tablename__ = "prompt_schema_migrations"

    version: Mapped[str] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptDefinition(Base):
    __tablename__ = "prompt_definitions"

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_key: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    purpose: Mapped[str]
    status: Mapped[str] = mapped_column(default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version_no"),
        UniqueConstraint("prompt_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt_definitions.id"))
    version_no: Mapped[int]
    version_label: Mapped[str]
    stage: Mapped[str]
    system_template: Mapped[str]
    user_template: Mapped[str]
    output_schema_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    content_hash: Mapped[str]
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"))
    change_summary: Mapped[str | None]
    created_by: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    def public(self) -> dict[str, Any]:
        return {
            **record(self),
            "output_schema_data": self.output_schema_json,
            "config_data": self.config_json,
        }


class PromptDeployment(Base):
    __tablename__ = "prompt_deployments"
    __table_args__ = (UniqueConstraint("prompt_id", "environment"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt_definitions.id"))
    environment: Mapped[str]
    current_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"))
    canary_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"))
    canary_percentage: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptDataset(Base):
    __tablename__ = "prompt_datasets"

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_definitions.id"))
    name: Mapped[str]
    description: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DatasetCase(Base):
    __tablename__ = "prompt_dataset_cases"
    __table_args__ = (Index("idx_prompt_cases_dataset", "dataset_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("prompt_datasets.id"))
    name: Mapped[str]
    input_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    expected_output_json: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)
    grading_rules_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    tags_json: Mapped[list] = mapped_column(JSONB, default=list)
    source_trace_id: Mapped[str | None]
    source_issue_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    def public(self) -> dict[str, Any]:
        return {
            **record(self),
            "input_json_data": self.input_json,
            "expected_output_data": self.expected_output_json,
            "grading_rules_data": self.grading_rules_json,
            "tags_data": self.tags_json,
        }


class EvaluationRun(Base):
    __tablename__ = "prompt_evaluation_runs"
    __table_args__ = (Index("idx_prompt_eval_version", "prompt_version_id", "started_at"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("prompt_datasets.id"))
    model_provider: Mapped[str]
    model_name: Mapped[str]
    status: Mapped[str]
    case_count: Mapped[int] = mapped_column(default=0)
    passed_count: Mapped[int] = mapped_column(default=0)
    pass_rate: Mapped[float] = mapped_column(default=0)
    average_score: Mapped[float] = mapped_column(default=0)
    total_cost: Mapped[float] = mapped_column(default=0)
    average_latency_ms: Mapped[float] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None]


class EvaluationResult(Base):
    __tablename__ = "prompt_evaluation_results"
    __table_args__ = (UniqueConstraint("evaluation_run_id", "case_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    evaluation_run_id: Mapped[str] = mapped_column(ForeignKey("prompt_evaluation_runs.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("prompt_dataset_cases.id"))
    status: Mapped[str]
    output_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    scores_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    total_score: Mapped[float] = mapped_column(default=0)
    passed: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    estimated_cost: Mapped[float | None]
    latency_ms: Mapped[float | None]
    error_message: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptExperiment(Base):
    __tablename__ = "prompt_experiments"
    __table_args__ = (Index("idx_prompt_experiments_prompt", "prompt_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt_definitions.id"))
    baseline_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    challenger_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("prompt_datasets.id"))
    status: Mapped[str]
    success_criteria_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    recommendation: Mapped[str | None]
    comparison_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def public(self) -> dict[str, Any]:
        return {
            **record(self),
            "success_criteria_data": self.success_criteria_json,
            "comparison_data": self.comparison_json,
        }


class ExperimentVariant(Base):
    __tablename__ = "prompt_experiment_variants"
    __table_args__ = (UniqueConstraint("experiment_id", "variant_key"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("prompt_experiments.id"))
    variant_key: Mapped[str]
    prompt_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    evaluation_run_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_evaluation_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromotionHistory(Base):
    __tablename__ = "prompt_promotion_history"

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt_definitions.id"))
    environment: Mapped[str]
    action: Mapped[str]
    from_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"))
    to_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"))
    reason: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OptimizationRun(Base):
    __tablename__ = "prompt_optimization_runs"

    id: Mapped[str] = mapped_column(primary_key=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt_definitions.id"))
    current_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_versions.id"))
    challenger_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("prompt_datasets.id"))
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_experiments.id"))
    status: Mapped[str]
    failures_json: Mapped[list] = mapped_column(JSONB, default=list)
    recommendation: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
