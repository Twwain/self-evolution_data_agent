"""
命名空间 + 数据源
命名空间是隔离域, 数据源是连接凭证.

Stage 1 Task 14: NamespaceRule 已废弃, 规则统一走 KnowledgeEntry[entry_type=rule].
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.crypto import EncryptedString
from app.models.base import Base, LOCAL_NOW


class Namespace(Base):
    __tablename__ = "namespaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)

    # ── 关联 ──
    datasources: Mapped[list["DataSource"]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan"
    )


class DataSource(Base):
    """数据源连接配置"""
    __tablename__ = "datasources"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"))
    db_type: Mapped[str] = mapped_column(String(20))  # mysql | mongodb
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int]
    database: Mapped[str] = mapped_column(String(100))
    username: Mapped[str] = mapped_column(String(100))
    password: Mapped[str] = mapped_column(EncryptedString)
    # 内省后存储的 schema 快照 — JSON 格式
    # MySQL:   {"tables": ["t1", "t2", ...]}
    # MongoDB: {"collections": ["c_promo", "c_sku", ...]}
    schema_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)

    namespace: Mapped["Namespace"] = relationship(back_populates="datasources")
