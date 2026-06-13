"""Stage 5 — execute_query(single) 结果截断时转 status=error 引导走 plan."""
from __future__ import annotations

from app.engine.tools.data_access_tools import _maybe_force_plan


def test_truncated_single_result_becomes_error():
    result = {"rows": [{"a": i} for i in range(1000)], "row_count": 1000, "truncated": True}
    out = _maybe_force_plan(result, mode="single")
    assert out.get("error") == "result_truncated_use_plan"
    assert "generate_query_plan" in out.get("suggestion", "")


def test_non_truncated_result_passes_through():
    result = {"rows": [{"a": 1}], "row_count": 1, "truncated": False}
    out = _maybe_force_plan(result, mode="single")
    assert out is result  # 原样透传, 零回归


def test_probe_count_modes_never_forced():
    result = {"rows": [{"a": i} for i in range(1000)], "row_count": 1000, "truncated": True}
    for mode in ("probe", "count", "batched"):
        out = _maybe_force_plan(result, mode=mode)
        assert out is result


def test_mongo_style_output_triggers_forced_plan():
    result = {"rows": [{"_id": "507f1f77bcf86cd799439011", "name": "x"} for _ in range(1000)],
              "row_count": 1000, "truncated": True}
    out = _maybe_force_plan(result, mode="single")
    assert out.get("error") == "result_truncated_use_plan"
    assert "generate_query_plan" in out.get("suggestion", "")
