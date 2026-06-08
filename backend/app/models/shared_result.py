"""分享结果 — 通过 token 公开访问查询快照"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW


class SharedResult(Base):
    __tablename__ = "shared_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(21), unique=True, index=True)  # nanoid
    query_history_id: Mapped[int] = mapped_column(
        ForeignKey("query_history.id", ondelete="CASCADE")
    )
    shared_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
