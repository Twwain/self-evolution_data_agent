"""Phase 2 — trace_extractor 单测.

验证 extract_collections / derive_cost_strategy / extract_join_fields / extract_final_pipeline
与 api/query.py 原实现行为一致.
"""

from app.knowledge.trace_extractor import (
    derive_cost_strategy,
    extract_collections,
    extract_final_pipeline,
    extract_join_fields,
)


# ── extract_collections ──

def test_extract_collections_dedupe_preserve_order():
    tool_trace = [
        {"name": "fetch_schema", "input": {"target": "c_a"}},
        {"name": "fetch_schema", "input": {"target": "c_b"}},
        {"name": "fetch_schema", "input": {"target": "c_a"}},  # dup
        {"name": "execute_query", "input": {"target": "c_c"}},
    ]
    assert extract_collections(tool_trace) == ["c_a", "c_b", "c_c"]


def test_extract_collections_skips_non_data_tools():
    tool_trace = [
        {"name": "lookup_knowledge", "input": {"query": "x"}},
        {"name": "save_knowledge", "input": {"content": "y"}},
        {"name": "fetch_schema", "input": {"target": "c_real"}},
    ]
    assert extract_collections(tool_trace) == ["c_real"]


def test_extract_collections_handles_empty():
    assert extract_collections([]) == []


def test_extract_collections_from_estimate_cost():
    tool_trace = [
        {"name": "estimate_cost", "input": {"target": "c_product"}},
    ]
    assert extract_collections(tool_trace) == ["c_product"]


# ── derive_cost_strategy ──

def test_derive_cost_strategy_count_only():
    tool_trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "count"}},
        {"name": "execute_query", "input": {"target": "c_b", "mode": "single"}},
    ]
    assert derive_cost_strategy(tool_trace) == "count_only_first"


def test_derive_cost_strategy_batched():
    tool_trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "batched"}},
    ]
    assert derive_cost_strategy(tool_trace) == "batched_count_only"


def test_derive_cost_strategy_default():
    tool_trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "single"}},
    ]
    assert derive_cost_strategy(tool_trace) == "default"


def test_derive_cost_strategy_no_execute():
    tool_trace = [
        {"name": "fetch_schema", "input": {"target": "c_a"}},
    ]
    assert derive_cost_strategy(tool_trace) == "default"


def test_derive_cost_strategy_batched_wins_over_count():
    tool_trace = [
        {"name": "execute_query", "input": {"mode": "count"}},
        {"name": "execute_query", "input": {"mode": "batched"}},
    ]
    assert derive_cost_strategy(tool_trace) == "batched_count_only"


# ── extract_join_fields ──

def test_extract_join_fields_from_plan():
    final_pipeline = {
        "type": "execute_plan",
        "steps": [
            {"step_idx": 0, "collection": "c_a"},
            {
                "step_idx": 1, "collection": "c_b",
                "pipeline": [
                    {"$lookup": {
                        "from": "c_a", "localField": "a_id", "foreignField": "_id",
                        "as": "joined",
                    }},
                ],
            },
        ],
    }
    out = extract_join_fields(final_pipeline)
    assert {"a": "c_b.a_id", "b": "c_a._id"} in out


def test_extract_join_fields_no_plan():
    assert extract_join_fields(None) == []
    assert extract_join_fields({"type": "other"}) == []


# ── extract_final_pipeline ──

def test_extract_final_pipeline_from_execute_plan():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_a"}},
        {"name": "execute_plan", "input": {"plan": {"steps": [
            {"step_idx": 1, "collection": "c_a"},
        ]}}, "output": {}},
    ]
    result = extract_final_pipeline(trace)
    assert result == {"type": "execute_plan", "steps": [{"step_idx": 1, "collection": "c_a"}]}


def test_extract_final_pipeline_takes_last():
    trace = [
        {"name": "execute_plan", "input": {"plan": {"steps": [{"step_idx": 1}]}}, "output": {}},
        {"name": "execute_plan", "input": {"plan": {"steps": [{"step_idx": 2}]}}, "output": {}},
    ]
    result = extract_final_pipeline(trace)
    assert result["steps"] == [{"step_idx": 2}]


def test_extract_final_pipeline_none_when_no_plan():
    trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "single"}, "output": {}},
    ]
    assert extract_final_pipeline(trace) is None
