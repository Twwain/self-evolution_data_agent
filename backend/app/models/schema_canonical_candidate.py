"""Schema canonical candidate — 所有抽取/合并产物的统一暂存层.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/02-data-model.md §2.1

生命周期 status:
  pending     → active        (汇聚 AUTO PROMOTE)
  pending     → in_conflict   (写入 Conflict 表)
  in_conflict → active        (Conflict resolve 选中)
  in_conflict → rejected      (Conflict resolve 否决)
  active      → superseded    (repo 重解析新候选替代)
  *           → orphaned      (repo / ds 被删)
"""
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW

CandidateStatus = Literal[
    "pending", "active", "in_conflict",
    "rejected", "superseding", "superseded", "orphaned",
]
CandidateKind = Literal[
    "table_description", "field_description",
    "enum_values", "relationship", "sample_values",
]
ConfidenceStatus = Literal[
    "confirmed_by_introspect", "confirmed_by_code", "confirmed_by_user",
    "evidence_only", "unverified",
]


class SchemaCanonicalCandidate(Base):
    __tablename__ = "schema_canonical_candidates"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "db_type", "database", "target",
            "field_path", "candidate_kind", "value_hash",
            name="uq_candidate_dedup",
        ),
        Index("idx_candidate_pending", "namespace_id", "status"),
        Index(
            "idx_candidate_field",
            "namespace_id", "db_type", "database", "target", "field_path", "candidate_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), index=True
    )

    db_type: Mapped[str] = mapped_column(String(16))
    database: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(200))
    field_path: Mapped[str] = mapped_column(String(200), default="")
    candidate_kind: Mapped[str] = mapped_column(String(32))

    candidate_value_json: Mapped[str] = mapped_column(Text)
    value_hash: Mapped[str] = mapped_column(String(64))
    evidence_sources_json: Mapped[str] = mapped_column(Text, default="[]")

    status: Mapped[str] = mapped_column(String(20), default="pending")
    confidence_status: Mapped[str] = mapped_column(String(32), default="unverified")

    repo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("git_repos.id", ondelete="SET NULL"), nullable=True
    )
    datasource_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("datasources.id", ondelete="SET NULL"), nullable=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=0)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
