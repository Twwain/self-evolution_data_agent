"""
KnowledgeAuditLog — 知识层操作审计 (Stage 1 引入)

每次状态转移 + 内容编辑 + 批量操作必写一条; 保留期由 IS_AUDIT_LOG_RETENTION_DAYS 控制.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LOCAL_NOW, Base


class KnowledgeAuditLog(Base):
    __tablename__ = "knowledge_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entries.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    """NULL = 跨 entry 批操作 (bulk_delete/bulk_reclass/bulk_reembed) 无单一锚点; 普通操作必填."""
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    """NULL = 系统 (agent / 后台任务 / 迁移脚本); 非 NULL = 人类操作员"""

    action: Mapped[str] = mapped_column(String(40))
    """∈ {propose, approve, reject, edit, supersede, restore,
          bulk_delete, bulk_reclass, bulk_reembed, expire, cancel,
          hard_delete (Stage 3 Task 6, 受 IS_KNOWLEDGE_HARD_DELETE_ENABLED 守护)}"""

    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    diff_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
