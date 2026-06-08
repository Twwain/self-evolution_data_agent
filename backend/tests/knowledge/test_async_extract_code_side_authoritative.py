"""Stage extractor-protocol Task 2 — 机械字段必须由代码抽取, 不经 LLM.

哲学: 机械字段不变性收敛在代码侧, LLM 只负责语义改写 (question_pattern + route_hint_reason).
"""
import pytest

from app.api.query import (
    _derive_cost_strategy,
    _extract_field_mappings,
    _extract_join_fields,
    _validate_llm_output_minimal,
)


def test_field_mappings_only_from_real_probe_calls():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_product"}, "output": {}},
        {
            "name": "inspect_values",
            "input": {"target": "c_product", "field": "categoryId"},
            "output": {},
        },
        {"name": "execute_plan", "input": {"plan": {}}, "output": {"rows": []}},
    ]
    out = _extract_field_mappings(trace)
    assert {"collection": "c_product", "field": ""} in out
    assert {"collection": "c_product", "field": "categoryId"} in out
    assert all(m["collection"] != "c_category" for m in out)


def test_cost_strategy_rules():
    assert _derive_cost_strategy([{"name": "execute_query", "input": {"mode": "single"}}]) == "default"
    assert _derive_cost_strategy([
        {"name": "execute_query", "input": {"mode": "count"}},
    ]) == "count_only_first"
    assert _derive_cost_strategy([
        {"name": "execute_query", "input": {"mode": "batched"}},
    ]) == "batched_count_only"


def test_join_fields_from_lookup_stage():
    pipeline = {
        "type": "execute_plan",
        "steps": [{
            "collection": "c_product",
            "pipeline": [{"$lookup": {
                "from": "c_category_group", "localField": "categoryId", "foreignField": "_id",
                "as": "category",
            }}],
        }],
    }
    out = _extract_join_fields(pipeline)
    assert out == [{"a": "c_product.categoryId", "b": "c_category_group._id"}]


def test_join_fields_empty_when_no_lookup():
    pipeline = {"type": "execute_plan", "steps": [{"collection": "c_product", "pipeline": []}]}
    assert _extract_join_fields(pipeline) == []


def test_join_fields_empty_when_pipeline_none():
    assert _extract_join_fields(None) == []


def test_validate_llm_output_minimal_accepts_two_fields():
    _validate_llm_output_minimal(
        {"question_pattern": "某商品的订单数量", "route_hint_reason": "两层关联"}
    )
    _validate_llm_output_minimal({"question_pattern": "某x", "route_hint_reason": None})


def test_validate_llm_output_minimal_rejects_missing_pattern():
    with pytest.raises(ValueError, match="question_pattern"):
        _validate_llm_output_minimal({"route_hint_reason": "x"})


def test_validate_llm_output_minimal_rejects_non_str_reason():
    with pytest.raises(ValueError, match="route_hint_reason"):
        _validate_llm_output_minimal({"question_pattern": "x", "route_hint_reason": 123})
