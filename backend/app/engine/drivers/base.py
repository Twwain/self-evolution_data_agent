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
class DataSourceDriver(Protocol):
    """所有数据源驱动必须实现此协议."""

    db_type: str

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
