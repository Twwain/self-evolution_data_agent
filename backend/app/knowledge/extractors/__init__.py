"""语言中立的 repo 解析数据契约 — agentic extractor 的中性输出层。

公开 API:
    from app.knowledge.extractors import (
        ParsedSchema, SchemaObject, FieldDef, EnumValue, RelSignal,
        KnowledgeProposal, ExtractorReport, validate_parsed_schema,
    )
    from app.knowledge.extractors.binding import PARADIGM_MAP

注: 旧 7-钩子确定性 SPI (engine/registry/protocol/declarative) 已随
2026-06-17-agentic-repo-extractor 作废, 仅保留纯 dataclass 契约 (base.py)
与 paradigm 路由 (binding.py)。
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
