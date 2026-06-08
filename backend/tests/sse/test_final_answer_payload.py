"""P0-5: final_answer 反扫 tool_trace 提取 rows/columns/chart_type 测试."""
from __future__ import annotations

from app.api.query import _extract_from_tool_trace
from app.engine.tools.registry import CHART_TOOLS, EXEC_TOOLS


class TestExtractFromToolTrace:
    def test_extract_rows_columns_from_last_ok_exec(self):
        trace = [
            {"name": "fetch_schema", "status": "ok",
             "input": {}, "output": {"fields": []}},
            {"name": "execute_query", "status": "ok",
             "input": {}, "output": {
                 "rows": [{"a": 1}, {"a": 2}],
                 "columns": ["a"],
                 "row_count": 2,
             }},
        ]
        result = _extract_from_tool_trace(trace, EXEC_TOOLS, ("rows", "columns"))
        assert result == {"rows": [{"a": 1}, {"a": 2}], "columns": ["a"]}

    def test_extract_skips_failed_exec(self):
        trace = [
            {"name": "execute_query", "status": "error",
             "input": {}, "output": {"error": "x"}},
            {"name": "execute_query", "status": "ok",
             "input": {}, "output": {"count": 42}},
        ]
        result = _extract_from_tool_trace(trace, EXEC_TOOLS, ("count",))
        assert result == {"count": 42}

    def test_extract_returns_empty_when_no_match(self):
        trace = [
            {"name": "fetch_schema", "status": "ok",
             "input": {}, "output": {"fields": []}},
        ]
        result = _extract_from_tool_trace(trace, EXEC_TOOLS, ("rows",))
        assert result == {}

    def test_extract_picks_last_when_multiple_ok(self):
        trace = [
            {"name": "execute_query", "status": "ok",
             "input": {}, "output": {"rows": [{"a": 1}], "columns": ["a"]}},
            {"name": "execute_query", "status": "ok",
             "input": {}, "output": {"rows": [{"b": 2}], "columns": ["b"]}},
        ]
        result = _extract_from_tool_trace(trace, EXEC_TOOLS, ("rows", "columns"))
        assert result == {"rows": [{"b": 2}], "columns": ["b"]}

    def test_extract_chart_type_from_recommend_chart(self):
        trace = [
            {"name": "execute_query", "status": "ok",
             "input": {}, "output": {"rows": [], "columns": []}},
            {"name": "recommend_chart", "status": "ok",
             "input": {}, "output": {"chart_type": "bar", "config": {}}},
        ]
        result = _extract_from_tool_trace(trace, CHART_TOOLS, ("chart_type",))
        assert result == {"chart_type": "bar"}

    def test_exec_tools_constant_includes_new_tools(self):
        assert "execute_query" in EXEC_TOOLS
        assert "execute_plan" in EXEC_TOOLS
        assert len(EXEC_TOOLS) == 2

    def test_chart_tools_constant_includes_recommend(self):
        assert "recommend_chart" in CHART_TOOLS
