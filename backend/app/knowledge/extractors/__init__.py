"""语言中立的 repo 解析数据契约 — agentic extractor 的中性输出层。

公开 API:
    from app.knowledge.extractors import (
        ParsedSchema, SchemaObject, FieldDef, EnumValue, RelSignal,
        KnowledgeProposal, ExtractorReport, validate_parsed_schema,
    )

注: 旧 7-钩子确定性 SPI (engine/registry/protocol/declarative) 与 binding.py (paradigm 路由
死码, 2026-06-20 删除) 已随 agentic-repo-extractor 作废。paradigm 路由现由
engine/drivers DRIVERS 注册表统一提供 (见 engine/db_types.py:PARADIGM_MAP)。
"""
from app.knowledge.extractors.base import (
    EnumValue,
    ExtractorReport,
    FieldDef,
    IndexDef,
    KnowledgeProposal,
    ParsedSchema,
    RelSignal,
    SchemaObject,
    validate_parsed_schema,
)

__all__ = [
    "EnumValue",
    "ExtractorReport",
    "FieldDef",
    "IndexDef",
    "KnowledgeProposal",
    "ParsedSchema",
    "RelSignal",
    "SchemaObject",
    "validate_parsed_schema",
]
