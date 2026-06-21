"""Test emit validation — 数据契约: 必备字段 + paradigm 合法值."""

from app.knowledge.extraction_emit import validate_emit

_VALID_OBJ = {
    "paradigm": "document",
    "kind": "collection",
    "name": "orders",
    "source_ref": "Order.java:1",
    "fields": [
        {"name": "orderNo", "type": "String"},
        {"name": "address", "type": "Address", "sub_fields": [
            {"name": "city", "type": "String"},
        ]}
    ]
}


class TestValidateEmit:
    def test_valid_object_passes(self):
        result = validate_emit(_VALID_OBJ)
        assert result.status == "ok"

    def test_valid_object_with_deep_nesting_passes(self):
        """6 层嵌套 — 深度不再拒绝, LLM 自觉 + dead_loop 兜底."""
        deep = _VALID_OBJ.copy()
        fld = {"name": "a", "type": "A", "sub_fields": [
            {"name": "b", "type": "B", "sub_fields": [
                {"name": "c", "type": "C", "sub_fields": [
                    {"name": "d", "type": "D", "sub_fields": [
                        {"name": "e", "type": "E", "sub_fields": [
                            {"name": "f", "type": "String"}
                        ]}
                    ]}
                ]}
            ]}
        ]}
        deep["fields"] = [fld]
        result = validate_emit(deep)
        assert result.status == "ok"

    def test_circular_type_reference_passes(self):
        """TreeNode→TreeNode 二次出现 — 不再拒绝, prompt 教 LLM 自觉停止."""
        circ = {
            "paradigm": "document",
            "kind": "collection",
            "name": "tree",
            "source_ref": "Tree.java:1",
            "fields": [
                {"name": "parent", "type": "TreeNode", "sub_fields": [
                    {"name": "children", "type": "TreeNode", "sub_fields": []}
                ]}
            ]
        }
        result = validate_emit(circ)
        assert result.status == "ok"

    def test_missing_required_name(self):
        obj = {k: v for k, v in _VALID_OBJ.items() if k != "name"}
        result = validate_emit(obj)
        assert result.status == "rejected"
        assert result.reason == "missing_required"

    def test_invalid_paradigm(self):
        obj = _VALID_OBJ.copy()
        obj["paradigm"] = "graph"
        result = validate_emit(obj)
        assert result.status == "rejected"
        assert result.reason == "invalid_paradigm"
