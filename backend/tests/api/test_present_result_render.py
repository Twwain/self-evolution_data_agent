"""Stage 3 — present_result.ref 反查 tool_trace 全量 rows + render_chart 集成."""
from __future__ import annotations

from app.api.query import _resolve_present_result


def _trace_dg_case():
    """复刻 D/G 案例结构 (用通用词): execute_query 返回完整多行, present_result 指向它."""
    full_rows = [
        {"day": "2024-01-01", "region": "north", "amount": 10},
        {"day": "2024-01-01", "region": "south", "amount": 20},
        {"day": "2024-01-02", "region": "north", "amount": 11},
        {"day": "2024-01-02", "region": "south", "amount": 21},
    ]
    return [
        {"id": "call_exec", "name": "execute_query", "status": "ok",
         "input": {}, "output": {"rows": full_rows, "row_count": 4}},
        {"id": "call_present", "name": "present_result", "status": "ok",
         "input": {}, "output": {"status": "ok", "ref": "call_exec",
                                 "chart_spec": {"chart_type": "line", "x": "day",
                                                "series_by": "region", "value": "amount"}}},
    ]


def test_resolve_renders_full_rows_multi_series():
    res = _resolve_present_result(_trace_dg_case())
    assert res is not None
    assert res["chart_type"] == "line"
    # 渲染用全量 4 行 → 2 series, 2 个去重 x
    assert len(res["chart_option"]["series"]) == 2
    assert res["chart_option"]["xAxis"]["data"] == ["2024-01-01", "2024-01-02"]
    # rows 是全量 (4 行), 非样本
    assert len(res["rows"]) == 4


def test_resolve_returns_none_without_present_result():
    trace = [{"id": "e1", "name": "execute_query", "status": "ok",
              "input": {}, "output": {"rows": [{"a": 1}]}}]
    assert _resolve_present_result(trace) is None


def test_resolve_invalid_ref_falls_back_to_table():
    trace = [
        {"id": "call_present", "name": "present_result", "status": "ok",
         "input": {}, "output": {"status": "ok", "ref": "nonexistent",
                                 "chart_spec": {"chart_type": "line", "x": "d", "value": "v"}}},
    ]
    res = _resolve_present_result(trace)
    # ref 失效 → 渲染端 fail-safe, chart_type=table, rows 空
    assert res["chart_type"] == "table"
    assert res["rows"] == []


def test_resolve_transmits_truncated_flag():
    """目标执行 output.truncated=True + total_row_count → 透传 (§4.6 不静默)."""
    full_rows = [{"day": f"2024-01-{i:02d}", "amount": i} for i in range(1, 11)]
    trace = [
        {"id": "call_exec", "name": "execute_plan", "status": "ok", "input": {},
         "output": {"rows": full_rows, "row_count": 10, "truncated": True,
                    "rendered_row_count": 10, "total_row_count": 1462}},
        {"id": "call_present", "name": "present_result", "status": "ok", "input": {},
         "output": {"status": "ok", "ref": "call_exec",
                    "chart_spec": {"chart_type": "line", "x": "day", "value": "amount"}}},
    ]
    res = _resolve_present_result(trace)
    assert res["truncated"] is True
    assert res["rendered_row_count"] == 10
    assert res["total_row_count"] == 1462  # 补 count 拿到的精确总数透传


def test_resolve_malformed_output_degrades_to_table():
    """畸形 output (非 dict / rows 非 list) 静默退化为空 rows → table, 不抛."""
    trace1 = [
        {"id": "e", "name": "execute_query", "status": "ok", "input": {}, "output": "garbage"},
        {"id": "p", "name": "present_result", "status": "ok", "input": {},
         "output": {"status": "ok", "ref": "e",
                    "chart_spec": {"chart_type": "line", "x": "d", "value": "v"}}},
    ]
    res1 = _resolve_present_result(trace1)
    assert res1["chart_type"] == "table"
    assert res1["rows"] == []
    trace2 = [
        {"id": "e", "name": "execute_query", "status": "ok", "input": {},
         "output": {"rows": {"not": "a list"}, "row_count": 0}},
        {"id": "p", "name": "present_result", "status": "ok", "input": {},
         "output": {"status": "ok", "ref": "e",
                    "chart_spec": {"chart_type": "bar", "x": "d", "value": "v"}}},
    ]
    res2 = _resolve_present_result(trace2)
    assert res2["chart_type"] == "table"
    assert res2["rows"] == []


def test_resolve_execute_plan_output_has_rows_key():
    """execute_plan 输出须含 rows 键, 与 execute_query 同形, 使 ref 反查统一."""
    trace = [
        {"id": "call_plan", "name": "execute_plan", "status": "ok",
         "input": {}, "output": {"rows": [{"day": "2024-01-01", "amount": 10}], "row_count": 1}},
        {"id": "call_present", "name": "present_result", "status": "ok",
         "input": {}, "output": {"status": "ok", "ref": "call_plan",
                                 "chart_spec": {"chart_type": "line", "x": "day", "value": "amount"}}},
    ]
    res = _resolve_present_result(trace)
    assert res["chart_type"] == "line"
    assert len(res["rows"]) == 1


def test_history_snapshot_uses_full_rows():
    """history 快照行数 = 完整结果行数, 非 LLM 手抄 (回归根因 2 落库 bug)."""
    presented = _resolve_present_result(_trace_dg_case())
    assert len(presented["rows"]) == 4  # 全量
    assert presented["chart_option"]["series"]  # 有渲染产物


def test_old_snapshot_without_chart_spec_renders_gracefully():
    """改前老快照(无 chart_spec, chart_option={}) 经 share 读路径 graceful fallback."""
    from app.schemas import QueryResponse
    old_snapshot = {
        "session_id": "s1", "history_id": 0, "generated_query": "SELECT ...",
        "columns": ["region", "amount"],
        "rows": [{"region": "north", "amount": 10}],  # 老: LLM 手抄样本
        "row_count": 1, "chart_type": "bar", "category_column": "region",
        "chart_option": {},  # 老: 恒为空
        "final_answer": "结果如下", "error": "",
    }
    snap = old_snapshot
    response_data = {
        "session_id": snap.get("session_id", ""),
        "history_id": 0, "needs_clarification": False,
        "clarification_message": snap.get("clarification_message", snap.get("final_answer", "")),
        "generated_query": snap.get("generated_query", ""),
        "columns": snap.get("columns", []), "rows": snap.get("rows", []),
        "row_count": snap.get("row_count", 0),
        "chart_type": snap.get("chart_type", "table"),
        "chart_option": snap.get("chart_option", {}),
        "performance_warning": snap.get("performance_warning", ""),
        "truncated": snap.get("truncated", False),
        "rendered_row_count": snap.get("rendered_row_count", 0),
        "total_row_count": snap.get("total_row_count", 0),
        "error": snap.get("error", ""),
        "clarification_questions": snap.get("clarification_questions", []),
        "pending_id": snap.get("pending_id", 0),
    }
    resp = QueryResponse(**response_data)
    assert resp.chart_type == "bar"
    assert resp.chart_option == {}  # 老快照空 option, 前端 ChartRenderer 兜底
    assert resp.rows == [{"region": "north", "amount": 10}]
    assert resp.truncated is False


def test_new_snapshot_round_trips_through_share_read_path():
    """新快照(渲染器全量 + 非空 chart_option) 经同一读路径正常."""
    from app.schemas import QueryResponse
    presented = _resolve_present_result(_trace_dg_case())
    resp = QueryResponse(
        session_id="", history_id=0, needs_clarification=False,
        clarification_message="", generated_query="",
        columns=presented["columns"], rows=presented["rows"],
        row_count=len(presented["rows"]), chart_type=presented["chart_type"],
        chart_option=presented["chart_option"],
        performance_warning="", error="", clarification_questions=[], pending_id=0,
    )
    assert resp.chart_type == "line"
    assert len(resp.chart_option["series"]) == 2  # 全量渲染产物保真


def test_extract_rows_chart_reads_chart_spec_chart_type():
    """知识抽取路径: present_result 的 chart_type 在 chart_spec 内, 不在顶层."""
    from app.api.query import _extract_rows_chart
    from app.engine.agent_loop import AgentResult
    result = AgentResult(
        final_answer="", iterations=1, stop_reason="end_turn",
        tool_trace=[
            {"id": "e", "name": "execute_query", "status": "ok", "input": {},
             "output": {"rows": [{"a": 1}, {"a": 2}], "row_count": 2}},
            {"id": "p", "name": "present_result", "status": "ok", "input": {},
             "output": {"status": "ok", "ref": "e",
                        "chart_spec": {"chart_type": "line", "x": "a"}}},
        ],
        usage_total={},
    )
    rows_count, chart_type = _extract_rows_chart(result)
    assert rows_count == 2
    assert chart_type == "line"  # 从 chart_spec 取, 非默认 table


def test_present_result_with_code_label_map():
    """Stage 4 — present_result 带 code_label_map 端到端兜底替换."""
    trace = [
        {"id": "e", "name": "execute_query", "status": "ok", "input": {},
         "output": {"rows": [{"region": "1", "amount": 10}, {"region": "2", "amount": 20}]}},
        {"id": "p", "name": "present_result", "status": "ok", "input": {},
         "output": {"status": "ok", "ref": "e", "chart_spec": {
             "chart_type": "bar", "x": "region", "value": "amount",
             "code_label_map": {"region": {"1": "north", "2": "south"}}}}},
    ]
    res = _resolve_present_result(trace)
    assert set(res["chart_option"]["xAxis"]["data"]) == {"north", "south"}
