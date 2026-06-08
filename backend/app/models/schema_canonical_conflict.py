"""Schema canonical conflict — 多候选不一致时的人工解决工作流.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/02-data-model.md §2.2
namespace+(db_type, database, target, field_path, candidate_kind) 维度的字段冲突载体. 单字段同时只能一个 open conflict (修订 #4: partial unique).
"""
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW

ConflictType = Literal["field_value", "semantic_equivalent"]
ConflictStatus = Literal["open", "resolved"]
ResolutionChoice = Literal["keep_a", "keep_b", "merge", "reject_all"]


class SchemaCanonicalConflict(Base):
    __tablename__ = "schema_canonical_conflicts"
    __table_args__ = (
        Index("idx_conflict_open", "namespace_id", "status"),
        # 修订 #4 partial unique: 仅 status='open' 行参与唯一约束.
        Index(
            "uq_one_open_conflict_per_field",
            "namespace_id", "db_type", "database", "target",
            "field_path", "candidate_kind",
            unique=True,
            sqlite_where=text("status = 'open'"),
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

    conflict_type: Mapped[str] = mapped_column(String(32))
    candidate_ids_json: Mapped[str] = mapped_column(Text)
    candidates_snapshot_json: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), default="open")
    resolution_choice: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolution_value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
