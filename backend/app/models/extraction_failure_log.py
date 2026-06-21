"""Extraction failure log — EXPLAIN 失败 / LLM 解析失败留痕, 不入 KE 表.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/02-data-model.md §2.7
"""
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW

ExtractionKind = Literal[
    "mybatis_example", "enum_class", "relationship", "where_evidence",
    "agentic_extraction",
]
FailureType = Literal[
    "unknown_table", "unknown_column", "syntax_error",
    "param_type_unresolved", "connection_error", "explain_timeout",
    "llm_parse_error", "ast_parse_error", "ast_diff_failed",
    "llm_rate_limited", "llm_server_error", "llm_timeout", "llm_4xx_error",
    "llm_empty_response",
]


class ExtractionFailureLog(Base):
    __tablename__ = "extraction_failure_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), index=True
    )
    repo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("git_repos.id", ondelete="SET NULL"), nullable=True
    )
    datasource_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("datasources.id", ondelete="SET NULL"), nullable=True
    )

    extraction_kind: Mapped[str] = mapped_column(String(32))
    source_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_mapper: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_method: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    failure_type: Mapped[str] = mapped_column(String(40))
    failure_message: Mapped[str] = mapped_column(Text)
    failure_extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
