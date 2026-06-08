"""TerminologyConflict — Phase 1 唯一键冲突载体 (与 SchemaCanonicalConflict 平行).

# ════════════════════════════════════════════
#  设计要点
# ════════════════════════════════════════════
# - 表已由 `schema_migrations.migration_008` 建好 (含 partial unique index +
#   ix_term_conflict_ns_status_created); 本模型仅做 ORM 映射, 不重声明索引/约束.
# - 时间戳走 `LOCAL_NOW` (`datetime('now','localtime')`) 与全项目惯例一致;
#   不用 `func.current_timestamp()`.
# - candidate_repo_id / resolved_by_id 配 ON DELETE SET NULL: 仓库或操作员注销
#   后冲突行保留, 候选/裁决人退化为系统态.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LOCAL_NOW, Base


class TerminologyConflict(Base):
    __tablename__ = "terminology_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("namespaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    existing_entry_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_payload: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_source: Mapped[str] = mapped_column(String(20), nullable=False)
    candidate_repo_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("git_repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolution_choice: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=LOCAL_NOW
    )
