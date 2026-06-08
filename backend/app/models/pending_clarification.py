"""
澄清 Pending — 结构化澄清流程的会话状态载体

生命周期:
1. execute_query 路由到 Decomposer → PreQuery 发现候选 N 条 (或 overflow / empty)
2. 建一条 pending_clarifications, status=pending, 返回 pending_id 给前端
3. 用户勾选 → POST /api/query/continue → 合并 resolved, 回跑主查询, status=resolved
4. expires_at 到期未选 → 后台定时任务清理

overflow 语义: 某 cond 爆集 (>阈值), 前端提示缩小范围, 仍返回 pending 允许用户刷新
abandoned 语义: 用户取消对话框, 前端可发 DELETE 标记 (P4 补)
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW


class PendingClarification(Base):
    __tablename__ = "pending_clarifications"
    __table_args__ = (
        Index("idx_pending_session", "session_id", "status"),
        Index("idx_pending_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64))
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"))
    original_question: Mapped[str] = mapped_column(Text)

    # Decomposer 输出的完整快照 (续跑时无需再调 LLM)
    targets_json: Mapped[str] = mapped_column(Text, default="[]")
    conditions_json: Mapped[str] = mapped_column(Text, default="[]")

    # 用户已选: cond_id → [value, ...] JSON
    resolved_json: Mapped[str] = mapped_column(Text, default="{}")
    # 仍待用户选的 cond_id 列表 JSON
    pending_cond_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    # 前端展示用 ClarifyQuestion 列表缓存 (避免续跑时再组装)
    clarification_questions_json: Mapped[str] = mapped_column(Text, default="[]")

    # pending | resolved | abandoned | overflow
    status: Mapped[str] = mapped_column(String(16), default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
