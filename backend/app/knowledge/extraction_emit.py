"""Emit 收口校验 — 纯数据契约: 必备字段 / paradigm 合法值.

不再做嵌套深度/循环引用检测。原因:
- LLM emit 的 JSON 直接入 PostgreSQL fields_json 列, 几层嵌套对系统无害。
- 深度上限通过 prompt ${max_depth} 软指引 LLM 自觉控制。
- 真死循环由 agent loop dead_loop 检测兜底 (连续重复工具调用)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmitResult:
    status: str               # ok | rejected
    reason: str = ""          # missing_required | invalid_paradigm
    at_field: str = ""        # 出错字段路径
    hint: str = ""


def validate_emit(data: dict) -> EmitResult:
    """校验 emit_schema_object 的输入 — 仅数据契约, 不做语义判断."""

    # 必备字段 (含 source_ref — 防 LLM 编造对象无源可溯)
    for req in ["paradigm", "kind", "name", "fields", "source_ref"]:
        if req not in data or (isinstance(data[req], (list, str)) and not data[req]):
            return EmitResult(
                status="rejected", reason="missing_required",
                at_field=req,
                hint=f"请提供 {req}。",
            )

    # paradigm 合法值
    if data["paradigm"] not in ("relational", "document"):
        return EmitResult(
            status="rejected", reason="invalid_paradigm",
            at_field="paradigm",
            hint="paradigm 必须是 'relational' 或 'document'。",
        )

    return EmitResult(status="ok")
