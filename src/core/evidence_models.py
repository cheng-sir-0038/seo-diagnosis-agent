from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EvidenceBase(DeclarativeBase):
    pass


class EvidenceSnapshot(EvidenceBase):
    __tablename__ = "evidence_snapshots"
    __table_args__ = (
        Index("idx_evidence_task", "task_id"),
        Index("idx_evidence_lookup", "provider", "capability", "params_hash"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[str]
    provider: Mapped[str]
    capability: Mapped[str]
    params_hash: Mapped[str | None]
    source: Mapped[str | None]
    fetched_at: Mapped[str | None]
    integrity: Mapped[str | None]
    is_estimated: Mapped[bool]
    units_consumed: Mapped[int] = mapped_column(BigInteger)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApiUsage(EvidenceBase):
    __tablename__ = "api_usage_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str]
    endpoint: Mapped[str]
    units_consumed: Mapped[int] = mapped_column(BigInteger)
    success: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
