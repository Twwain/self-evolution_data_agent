"""SPI 数据契约 — extractor 的中性输出, 镜像 SchemaCanonicalObject definition。

db_type 产品与 database 不在此层 (部署事实, 由 binding 步骤填); 此层只认 paradigm。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Paradigm 是开放集: 新增范式 (kv/search/graph) 时扩这里 + 下游处理, 不破坏既有 extractor
Paradigm = str
# VALID_PARADIGMS 从 engine.db_types 导入 (单一真相源, 派生自 DRIVERS 注册表).
# extractors 层保留 import 网关以防 engine 层 protocol 语义不同步, 不做二次定义.
from app.engine.db_types import VALID_PARADIGMS  # noqa: E402 — 紧贴 Paradigm type alias, 有意放在顶部 imports 之后


@dataclass
class EnumValue:
    name: str
    db_value: object
    description: str | None = None


@dataclass
class FieldDef:
    name: str
    type: str
    description: str | None = None
    nullable: bool | None = None
    indexed: bool | None = None
    enum_values: list[EnumValue] = field(default_factory=list)
    sub_fields: list["FieldDef"] = field(default_factory=list)
    # L1/L3 enum resolution hints — extractor 在 normalize 阶段从 LLM 输出回填,
    # 供 capabilities/enum 的四层 fallback 使用 (与现 enum_extractor 语义一致)
    enum_class_hint: str | None = None   # L1: LLM 标注的枚举类名 (仅 hint, 不可靠)
    raw_type: str | None = None          # L3: 字段声明的原始泛型类型 (e.g. "List<Foo>")
    # 枚举解析结果元数据 — enrich_enum_values 回填, 供 _rebuild_enum_classes_from_parsed 使用
    _enum_class_name: str | None = None  # 命中枚举的 simple class name (e.g. "OrderStatus")


@dataclass
class IndexDef:
    name: str
    columns: list[str] = field(default_factory=list)
    unique: bool = False


@dataclass
class SchemaObject:
    paradigm: Paradigm
    kind: str                       # "table" | "collection"
    name: str
    description: str | None = None
    fields: list[FieldDef] = field(default_factory=list)
    indexes: list[IndexDef] = field(default_factory=list)
    source_ref: str | None = None   # 来源文件, 供审计/调试


@dataclass
class RelSignal:
    from_object: str
    from_field: str
    to_object: str
    to_field: str
    relation_type: str              # foreign_key | jpa | dbref | mybatis_join | dao_relation
    confidence: str = "explicit"    # explicit | weak
    from_db_type: str | None = None   # mysql | mongodb | ... (binding 步骤填)
    to_db_type: str | None = None
    from_database: str | None = None
    to_database: str | None = None
    is_required: bool = False
    hit_count: int | None = None      # MyBatis JOIN 命中次数 (弱信号阈值用)
    mapper: str | None = None         # MyBatis mapper 标识 (弱信号去重用)
    evidence: list[dict] = field(default_factory=list)  # [{source, ...}]


@dataclass
class KnowledgeProposal:
    entry_type: str                 # route_hint | terminology
    payload: dict = field(default_factory=dict)


@dataclass
class ExtractorReport:
    extractor: str
    status: str = "ok"              # ok | partial | failed
    objects_count: int = 0
    files_parsed: int = 0           # 已解析的文件数 (≠ objects_count)
    errors: list[str] = field(default_factory=list)


@dataclass
class ParsedSchema:
    objects: list[SchemaObject] = field(default_factory=list)
    relationships: list[RelSignal] = field(default_factory=list)
    knowledge_proposals: list[KnowledgeProposal] = field(default_factory=list)
    where_evidence: list[dict] = field(default_factory=list)  # WHERE 字面量证据
    report: ExtractorReport | None = None
    # 以下 CodeParseResult 通道有意不设:
    #   mongo_query_patterns — code_parser 内部统计用, 无下游消费者
    #   business_terms_candidates — code_parser 从未填充 (dead field)
    #   business_rules_candidates — code_parser 从未填充 (dead field)


def validate_parsed_schema(ps: ParsedSchema) -> list[str]:
    """契约校验: paradigm 合法 + MUST 字段 (对象名 / 字段 name+type) 非空。返回错误列表。"""
    errors: list[str] = []
    for i, obj in enumerate(ps.objects):
        if obj.paradigm not in VALID_PARADIGMS:
            errors.append(f"objects[{i}].paradigm invalid: {obj.paradigm!r}")
        if not obj.name:
            errors.append(f"objects[{i}].name empty")
        for j, fld in enumerate(obj.fields):
            if not fld.name:
                errors.append(f"objects[{i}].fields[{j}].name empty")
            if not fld.type:
                errors.append(f"objects[{i}].fields[{j}].type empty")
    return errors
