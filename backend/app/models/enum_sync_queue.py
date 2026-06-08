"""Enum 同步队列 — EnumDictionary 变更事件投递.

设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/04-field-enum-binding.md §4.4
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW


class EnumSyncQueue(Base):
    __tablename__ = "enum_sync_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    enum_dict_id: Mapped[int] = mapped_column(Integer, index=True)
    namespace_id: Mapped[int] = mapped_column(Integer, index=True)
    event: Mapped[str] = mapped_column(String(20))  # create | update | delete
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
