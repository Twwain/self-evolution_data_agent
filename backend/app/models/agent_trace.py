"""AgentTrace — Stage 2 抓手 E: agent_loop 完整 trace 持久化, 供手动批量提炼用."""

from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LOCAL_NOW, Base

AgentTraceStatus = Literal["completed", "cancelled", "failed", "refined"]


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    """会话标识 — 同一会话多次执行共享, 用于按会话聚合 (nullable 兼容旧行)."""
    namespace_id: Mapped[int | None] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=True,
    )
    user_query: Mapped[str] = mapped_column(Text, default="")
    trace_json: Mapped[str] = mapped_column(Text, default="{}")
    """完整 tool 序列 + LLM thinking + final_answer."""

    reflection_log_json: Mapped[str] = mapped_column(Text, default="[]")
    """Task C 写入: agent 决策时的 reflection 块 (confidence + reason + alternative)."""

    status: Mapped[str] = mapped_column(String(16), default="completed")
    refined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refined_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
