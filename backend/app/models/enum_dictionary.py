"""Enum 字典独立载体 — class 视角.

设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/03-enum-dictionary.md
"""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW


class EnumDictionary(Base):
    __tablename__ = "enum_dictionaries"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id",
            "enum_class_name",
            name="uq_enum_dict_ns_name",
        ),
        Index("idx_enum_dict_source", "namespace_id", "source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"),
        index=True,
    )
    enum_class_name: Mapped[str] = mapped_column(String(100))
    fully_qualified_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    values_json: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(20), default="namespace")
    source: Mapped[str] = mapped_column(String(20), default="code")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    created_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
