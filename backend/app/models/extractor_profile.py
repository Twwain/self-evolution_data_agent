"""Profile model — extraction hint 可选叠层."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW, local_now


class ExtractorProfile(Base):
    __tablename__ = "extractor_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    languages: Mapped[list] = mapped_column(JSONB, default=lambda: ["Java"], nullable=False)
    hint_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # NULL = 全局 (builtin profiles 对所有 namespace 可见)
    namespace_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=LOCAL_NOW, default=local_now, onupdate=local_now
    )

    __table_args__ = (
        UniqueConstraint("name", "namespace_id", name="uq_profile_name_ns"),
    )
