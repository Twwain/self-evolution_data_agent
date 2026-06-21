"""
解析结果数据结构 — 代码解析 + 质量评估的统一数据模型
CodeParseResult 与 schema_builder 的输入格式严格兼容
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileParseResult:
    """单文件解析状态"""
    file_path: str
    status: str = "ok"  # ok | skipped | error
    reason: str = ""
    items_count: int = 0


@dataclass
class ParserStats:
    """解析统计 — 全局视角的数字"""
    files_scanned: int = 0
    files_parsed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    items_extracted: int = 0
    tables_found: list[str] = field(default_factory=list)
    error_details: list[FileParseResult] = field(default_factory=list)


@dataclass
class CodeParseResult:
    """
    统一解析输出 — 兼容 schema_builder 期望的输入格式
    mybatis_entries: build_example_sql_from_mybatis() 的输入
    jpa_entities:    build_ddl_from_jpa() + build_doc_from_jpa() 的输入
    mongo_documents: build_doc_from_mongo() 的输入
    """
    mybatis_entries: list[dict] = field(default_factory=list)
    jpa_entities: list[dict] = field(default_factory=list)
    mongo_documents: list[dict] = field(default_factory=list)
    mongo_query_patterns: list[dict] = field(default_factory=list)
    # ── Phase 2 新增抽取通道 ──
    enum_classes: list[dict] = field(default_factory=list)
    where_evidence: list[dict] = field(default_factory=list)
    business_terms_candidates: list[dict] = field(default_factory=list)
    business_rules_candidates: list[dict] = field(default_factory=list)


@dataclass
class ParseReport:
    """完整解析报告 — 从解析到评估的全链路结果"""
    repo_id: int = 0
    duration_seconds: float = 0.0
    stats: ParserStats = field(default_factory=ParserStats)
    ddls_trained: int = 0
    docs_trained: int = 0
    sqls_trained: int = 0
    query_patterns_trained: int = 0
    completeness_score: int = 0       # 0-100
    evaluation_summary: str = ""
