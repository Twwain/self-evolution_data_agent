"""Enum 绑定冲突 — sample_values 不覆盖时记录.

设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/04-field-enum-binding.md §5
Partial unique index: 同 (field_canonical_id, field_name, enum_dict_id) 同时只能有一条 open.
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW


class EnumBindingConflict(Base):
    __tablename__ = "enum_binding_conflicts"
    __table_args__ = (
        Index(
            "uq_enum_conflict_open",
            "field_canonical_id", "field_name", "enum_dict_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(Integer, index=True)
    field_canonical_id: Mapped[int] = mapped_column(Integer)
    field_name: Mapped[str] = mapped_column(String(100))
    enum_dict_id: Mapped[int] = mapped_column(Integer)
    conflict_kind: Mapped[str] = mapped_column(String(30))
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
