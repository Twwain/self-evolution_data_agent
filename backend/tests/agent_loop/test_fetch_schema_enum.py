"""Task 3: _filter_enum_fields_for_llm 单元测试.

验证 fetch_schema 输出过滤:
- matched 保留 enum_values, 去掉内部字段
- pending/conflict 去掉 enum_values + 内部字段
- 无 enum 字段原样返回
- sample_values / sample_metadata 不外泄
"""
from app.engine.tools.data_access_tools import _filter_enum_fields_for_llm


def test_matched_keeps_enum_values():
    f = {
        "name": "status",
        "type": "Integer",
        "enum_values": [{"name": "A", "db_value": 1}],
        "enum_match_status": "matched",
        "enum_ref_id": 42,
        "enum_source": "code_hint",
        "enum_class_hint": "OrderStatus",
    }
    out = _filter_enum_fields_for_llm(f)
    assert out["enum_values"] == [{"name": "A", "db_value": 1}]
    # 内部字段不外泄
    for k in ("enum_ref_id", "enum_source", "enum_match_status", "enum_class_hint"):
        assert k not in out


def test_pending_strips_enum_values():
    f = {
        "name": "x",
        "type": "Integer",
        "enum_values": [{"name": "OLD", "db_value": 0}],
        "enum_match_status": "pending",
    }
    out = _filter_enum_fields_for_llm(f)
    assert "enum_values" not in out


def test_conflict_strips_enum_values():
    f = {
        "name": "x",
        "type": "Integer",
        "enum_values": [{"name": "OLD", "db_value": 0}],
        "enum_match_status": "conflict",
    }
    out = _filter_enum_fields_for_llm(f)
    assert "enum_values" not in out


def test_no_enum_unchanged():
    f = {"name": "amount", "type": "Long"}
    out = _filter_enum_fields_for_llm(f)
    assert out == {"name": "amount", "type": "Long"}


def test_sample_values_not_exposed():
    f = {
        "name": "x",
        "type": "Integer",
        "sample_values": [0, 1],
        "sample_metadata": {"count": 100},
        "enum_match_status": "matched",
        "enum_values": [{"name": "A", "db_value": 0}],
    }
    out = _filter_enum_fields_for_llm(f)
    assert "sample_values" not in out
    assert "sample_metadata" not in out
    assert out["enum_values"] == [{"name": "A", "db_value": 0}]
