"""跨数据库 schema canonical 真相源 (统一 MySQL + MongoDB 视图).

设计: docs/superpowers/specs/2026-05-09-agent-loop-multi-driver/plans/2026-05-09-stage2-schema-canonical.md

一张表汇入 MySQL + MongoDB (+ 未来 PostgreSQL 等) 的 schema 信息:
- MySQL: 直接从 INFORMATION_SCHEMA 写入, 无 fragment / 冲突表
- MongoDB: 经 promote_candidates_to_canonical 写入 (Phase 4 + mongo-canonical-retirement 完成统一)

用户可在前端编辑 field description, 编辑后 reviewed=True.
fetch_schema 工具优先读此表, fallback driver introspect.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW


class SchemaCanonicalObject(Base):
    __tablename__ = "schema_canonical_objects"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "db_type", "database", "target",
            name="uq_sco_ns_dbtype_db_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"))
    db_type: Mapped[str] = mapped_column(String(20))     # "mysql" | "mongodb" | ...
    database: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(200))     # 表名 / 集合名

    # 字段定义 (JSON 数组)
    # [{"name":"col","type":"VARCHAR(100)","description":"用户编辑","indexed":true,"nullable":true}]
    fields_json: Mapped[str] = mapped_column(Text, default="[]")

    # 索引定义 (JSON 数组)
    # [{"name":"idx_col","columns":["col"],"unique":false}]
    indexes_json: Mapped[str] = mapped_column(Text, default="[]")

    description: Mapped[str] = mapped_column(Text, default="")
    purpose_detail: Mapped[str] = mapped_column(Text, default="")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_count: Mapped[int] = mapped_column(default=0)

    # 来源: introspect | merge | manual
    source: Mapped[str] = mapped_column(String(20), default="introspect")

    # ── Phase 1 新增 (schema-knowledge-onboarding §2.4) ──
    relationships_json: Mapped[str] = mapped_column(Text, default="[]")
    sample_values_json: Mapped[str] = mapped_column(Text, default="[]")
    user_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
