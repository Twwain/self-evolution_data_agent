"""DataSourceDriver Protocol + 共享数据结构."""
from __future__ import annotations

from typing import Literal, Protocol, TypedDict, runtime_checkable

from app.models import DataSource


class FieldDef(TypedDict):
    name: str
    type: str
    description: str
    indexed: bool
    nullable: bool


class SchemaSnapshot(TypedDict):
    db_type: str
    database: str
    target: str
    description: str
    fields: list[FieldDef]
    indexes: list[dict]
    sample_count: int


class CostEstimate(TypedDict):
    estimated_rows: int
    warning_level: Literal["ok", "high", "blocked"]
    raw_explain: dict


class EquivalentHint(TypedDict):
    """命中某能力限制时, 暴露给 LLM 的等效语法提示 (R2.6).

    restriction: 触发该 hint 的限制标识 (算子名 / stage variant id / syntax constraint id)
    suggestion:  等效表达建议文本 (供 LLM 改写 pipeline)
    """
    restriction: str
    suggestion: str


class ServerCapabilities(TypedDict):
    """Server version + flavor-aware capability restrictions. driver-agnostic shape.

    三类能力限制 (Capability_Restrictions) 覆盖本特性确认的三类 DocumentDB 错误:
    - unsupported_ops:            不支持的聚合算子扁平名 (如 $getField, 错误码 5654600)
    - unsupported_stage_variants: 不支持的 stage 变体/选项形态 (如 $lookup.let_pipeline, 错误码 304)
    - syntax_constraints:         语法约束 (如 project_no_dollar_fieldpath, 错误码 16410)

    agg_ops_unsupported 为 unsupported_ops 的向后兼容别名 (同值填充), 标注 deprecated:
    保留是因 registry.py 系统提示与下游消费方仍按旧名引用; 新代码应读三类限制字段.
    """
    version: str
    flavor: str
    unsupported_ops: list[str]
    unsupported_stage_variants: list[str]
    syntax_constraints: list[str]
    equivalent_hints: list[EquivalentHint]
    agg_ops_unsupported: list[str]  # deprecated alias == unsupported_ops


ExecuteMode = Literal["single", "probe", "count", "batched", "render"]


class ExecuteResult(TypedDict):
    rows: list[dict]
    row_count: int
    truncated: bool
    elapsed_ms: int


@runtime_checkable
class SqlDataSourceDriver(Protocol):
    """SQL 型数据源驱动额外约定: 暴露行数保护剥离方法, 供 plan_executor render/count 路径使用.

    MySQL 与 Oracle driver 均需实现此方法; MongoDB driver 不实现.
    plan_executor 在 SQL 分支通过 get_driver(db_type).strip_outer_row_limit(sql) 调用,
    不 import 任何具体 driver 类.
    """

    def strip_outer_row_limit(self, sql: str) -> str:
        """剥离最外层行数保护 (MySQL LIMIT / Oracle ROWNUM wrapper), 供 executor render/count 用."""
        ...


@runtime_checkable
class DataSourceDriver(Protocol):
    """所有数据源驱动必须实现此协议."""

    db_type: str
    paradigm: str  # "relational" | "document" — 知识挂在实体上, 由 driver 类声明

    async def list_object_names(self, ds: DataSource) -> list[str]:
        """连库列所有表/集合名. 用于反查 (object_name → database) 绑定.

        连接失败抛异常, 由调用方隔离.
        """
        ...

    async def fetch_schema(
        self,
        ds: DataSource,
        target: str | None = None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        ...

    async def inspect_values(
        self,
        ds: DataSource,
        target: str,
        field: str,
        limit: int = 10,
    ) -> list[dict]:
        ...

    async def estimate_cost(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> CostEstimate:
        ...

    async def execute_query(
        self,
        ds: DataSource,
        target: str,
        query: dict,
        mode: ExecuteMode = "single",
        batch_size: int = 1000,  # noqa: hardcode
    ) -> ExecuteResult:
        ...

    async def health_check(self, ds: DataSource) -> bool:
        ...

    async def get_server_capabilities(
        self, ds: DataSource,
    ) -> ServerCapabilities | None:
        """Return server version + version-gated features. None if not applicable."""
        ...

    async def fetch_db_profile(self, ds: DataSource) -> dict:
        """连库合成库级画像 (版本/字符集或flavor/对象数量).

        ⚠️ 走一次性临时连接, 不进 ds.id 缓存池 (建源时 ds.id 尚为 None).
        降级语义: 每个子查询独立 try, 抓到几个算几个, 永不抛异常.
        返回 dict 缺某键 = 该项抽取失败, 不影响其他键. profiled_at 始终有.
        """
        ...

    async def fetch_foreign_keys(
        self, ds: DataSource, target: str | None = None,
    ) -> list[dict]:
        """返回外键关系列表. 每项含 from_target/from_field/to_db_type/to_database/
        to_target/to_field/relation_type. 不支持外键的 driver 显式 return []."""
        return []
