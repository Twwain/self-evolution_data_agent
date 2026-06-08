"""Task 1: _set_field_attr + _bind_field_to_enum helper 单元测试."""
import json

from app.knowledge.canonical_promote import _bind_field_to_enum, _set_field_attr


def test_set_field_attr_creates_field():
    fields_json = "[]"
    out = _set_field_attr(fields_json, "status", "enum_match_status", "pending")
    fields = json.loads(out)
    assert fields[0]["name"] == "status"
    assert fields[0]["enum_match_status"] == "pending"


def test_set_field_attr_updates_existing():
    fields_json = json.dumps([{"name": "status", "type": "Integer"}])
    out = _set_field_attr(fields_json, "status", "enum_class_hint", "OrderStatus")
    fields = json.loads(out)
    assert fields[0]["enum_class_hint"] == "OrderStatus"
    assert fields[0]["type"] == "Integer"


def test_bind_field_to_enum_writes_all_attrs():
    fields_json = json.dumps([{"name": "status", "type": "Integer"}])
    out = _bind_field_to_enum(
        fields_json, "status",
        enum_ref_id=42,
        enum_values=[{"name": "A", "db_value": 1}],
        enum_source="code_hint",
        enum_match_status="matched",
    )
    f = json.loads(out)[0]
    assert f["enum_ref_id"] == 42
    assert f["enum_values"] == [{"name": "A", "db_value": 1}]
    assert f["enum_source"] == "code_hint"
    assert f["enum_match_status"] == "matched"


def test_bind_field_to_enum_creates_field_if_missing():
    fields_json = "[]"
    out = _bind_field_to_enum(
        fields_json, "newField",
        enum_ref_id=99,
        enum_values=[{"name": "X", "db_value": 0}],
        enum_source="name_heuristic",
        enum_match_status="matched",
    )
    fields = json.loads(out)
    assert len(fields) == 1
    assert fields[0]["name"] == "newField"
    assert fields[0]["enum_ref_id"] == 99
