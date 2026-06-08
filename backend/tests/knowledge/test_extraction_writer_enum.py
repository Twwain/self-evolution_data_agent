"""extraction_writer._build_field_payload_for_candidate 翻译 helper 测试.

覆盖:
- _enum_class_name → enum_class_hint, _enum_source → enum_source
- _enum_match_status → enum_match_status
- 无 enum 标记时 payload 干净
"""
from app.knowledge.extraction_writer import _build_field_payload_for_candidate


def test_translates_enum_meta():
    field = {
        "field": "status",
        "type": "Integer",
        "enum_values": [{"name": "A", "db_value": 1}],
        "_enum_source": "code_hint",
        "_enum_class_name": "OrderStatus",
    }
    payload = _build_field_payload_for_candidate(field)
    assert payload["enum_class_hint"] == "OrderStatus"
    assert payload["enum_source"] == "code_hint"
    assert payload["enum_values"] == [{"name": "A", "db_value": 1}]
    assert "_enum_source" not in payload
    assert "_enum_class_name" not in payload


def test_pending_status_translated():
    field = {
        "field": "moduleType",
        "type": "Integer",
        "_enum_match_status": "pending",
    }
    payload = _build_field_payload_for_candidate(field)
    assert payload["enum_match_status"] == "pending"
    assert "enum_values" not in payload


def test_no_enum_meta_clean_payload():
    field = {"field": "amount", "type": "Long"}
    payload = _build_field_payload_for_candidate(field)
    assert "enum_values" not in payload
    assert "enum_match_status" not in payload
    assert "enum_class_hint" not in payload
