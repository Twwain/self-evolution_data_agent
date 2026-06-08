"""_normalize_bson 测试 — DBRef/ObjectId 序列化为 LLM 友好结构 (改动 A).

cd backend && python -m pytest tests/drivers/test_bson_normalize.py --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import datetime

from bson import DBRef, ObjectId
from bson.decimal128 import Decimal128

from app.engine.drivers.mongo import _normalize_bson


def test_dbref_to_structured():
    oid = ObjectId("611dc8215a711500018f1fe4")
    out = _normalize_bson(DBRef("c_module", oid))
    assert out == {
        "$ref": "c_module",
        "$id_str": "611dc8215a711500018f1fe4",
        "$id_type": "ObjectId",
    }


def test_nested_objectid_to_str():
    oid = ObjectId("611dc8215a711500018f1fe4")
    out = _normalize_bson({"_id": oid, "inner": {"x": oid}, "arr": [oid]})
    assert out["_id"] == "611dc8215a711500018f1fe4"
    assert out["inner"]["x"] == "611dc8215a711500018f1fe4"
    assert out["arr"][0] == "611dc8215a711500018f1fe4"


def test_dbref_inside_array_of_objects():
    # 复现 trace 形态: materialModules[].module 是 DBRef
    oid = ObjectId("611dc8215a711500018f1fe4")
    doc = {"materialModules": [{"module": DBRef("c_module", oid)}]}
    out = _normalize_bson(doc)
    mod = out["materialModules"][0]["module"]
    assert mod["$ref"] == "c_module"
    assert mod["$id_str"] == "611dc8215a711500018f1fe4"


def test_dbref_with_string_id():
    # DBRef.id 不一定是 ObjectId, 可能是字符串
    out = _normalize_bson(DBRef("c_x", "doc-123"))
    assert out == {"$ref": "c_x", "$id_str": "doc-123", "$id_type": "str"}


def test_decimal128_and_bytes_stringified():
    assert _normalize_bson(Decimal128("3.14")) == "3.14"
    assert isinstance(_normalize_bson(b"\x00\x01"), str)


def test_primitives_passthrough():
    assert _normalize_bson("abc") == "abc"
    assert _normalize_bson(42) == 42
    assert _normalize_bson(3.5) == 3.5
    assert _normalize_bson(True) is True
    assert _normalize_bson(None) is None


def test_datetime_passthrough_for_downstream():
    # datetime 非 BSON 特殊类, 原样返回 (下游 json default=str 兜底)
    dt = datetime.datetime(2020, 1, 1)
    assert _normalize_bson(dt) is dt


def test_no_dbref_string_blob_remains():
    # 关键回归: 输出里不应再出现 "DBRef(...)" 字符串黑盒
    import json
    oid = ObjectId("611dc8215a711500018f1fe4")
    doc = {"materialModules": [{"module": DBRef("c_module", oid)}]}
    out_str = json.dumps(_normalize_bson(doc), default=str)
    assert "DBRef(" not in out_str
    assert "ObjectId(" not in out_str
    assert "611dc8215a711500018f1fe4" in out_str  # 裸 id 可取


def test_depth_guard_no_stack_overflow():
    # 病态深嵌套不爆栈: 超过 _BSON_MAX_DEPTH 降级为 str(), 不抛异常
    from app.engine.drivers.mongo import _BSON_MAX_DEPTH
    root = cur = {}
    for _ in range(_BSON_MAX_DEPTH + 50):
        cur["n"] = {}
        cur = cur["n"]
    out = _normalize_bson(root)  # 不应 RecursionError
    assert isinstance(out, dict)


def test_primitive_fast_path():
    # fast-path 基础标量原样返回 (不经特殊类型分支)
    assert _normalize_bson("x") == "x"
    assert _normalize_bson(0) == 0
    assert _normalize_bson(False) is False
    assert _normalize_bson(None) is None
