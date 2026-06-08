"""
Phase 2 P2.T1 — enum 专精解析基础设施测试

覆盖:
- _scan_enum_classes: regex 扫描 enum 文件
- _parse_enum_classes_batch: LLM 解析 enum (mock LLM)
- _enrich_entity_fields_with_enum_index: 字段注入
- enum_class_index 双索引 (simple_name + fqn)
- LLM 错误容错
"""

import json
from unittest.mock import patch

import pytest

from app.knowledge.code_parser import (
    _parse_enum_classes_batch,
    _scan_enum_classes,
)
from app.knowledge.enum_extractor import (
    EnumDef,
    EnumValue,
    build_enum_class_index,
    enrich_entity_fields_with_enum_index,
    scan_enum_classes,
)


# ════════════════════════════════════════════
#  _scan_enum_classes
# ════════════════════════════════════════════


class TestScanEnumClasses:
    """regex 扫描 enum 文件"""

    def test_finds_enum_files(self, tmp_path):
        """包含 public enum 的文件被识别"""
        enum_file = tmp_path / "OrderStatus.java"
        enum_file.write_text(
            "package com.example;\n\n"
            "public enum OrderStatus {\n"
            "    CREATED(1, \"已创建\"),\n"
            "    PAID(2, \"已支付\");\n"
            "}\n"
        )
        non_enum = tmp_path / "Order.java"
        non_enum.write_text(
            "package com.example;\n\n"
            "@Document\n"
            "public class Order {\n"
            "    private OrderStatus status;\n"
            "}\n"
        )

        result = _scan_enum_classes([str(enum_file), str(non_enum)])
        assert result == [str(enum_file)]

    def test_skips_non_public_enum(self, tmp_path):
        """非 public enum 不被识别 (private/package-private)"""
        private_enum = tmp_path / "InternalStatus.java"
        private_enum.write_text(
            "package com.example;\n\n"
            "enum InternalStatus { A, B }\n"
        )

        result = _scan_enum_classes([str(private_enum)])
        assert result == []

    def test_empty_file_list(self):
        """空文件列表返回空"""
        assert _scan_enum_classes([]) == []

    def test_unreadable_file_skipped(self, tmp_path):
        """不可读文件被跳过, 不抛异常"""
        result = _scan_enum_classes([str(tmp_path / "nonexistent.java")])
        assert result == []


# ════════════════════════════════════════════
#  _parse_enum_classes_batch
# ════════════════════════════════════════════


class TestParseEnumClassesBatch:
    """LLM 解析 enum (mock chat_completion)"""

    def _make_enum_file(self, tmp_path, filename, content):
        f = tmp_path / filename
        f.write_text(content)
        return str(f)

    def test_extracts_values(self, tmp_path):
        """正常 LLM 响应 → 正确提取 enum_classes 和 index"""
        enum_file = self._make_enum_file(
            tmp_path, "OrderStatus.java",
            "package com.example;\n\npublic enum OrderStatus {\n"
            "    CREATED(1, \"已创建\"),\n    PAID(2, \"已支付\");\n}\n"
        )
        llm_response = json.dumps({
            "enum_class": "OrderStatus",
            "fully_qualified_name": "com.example.OrderStatus",
            "values": [
                {"name": "CREATED", "db_value": 1, "description": "已创建"},
                {"name": "PAID", "db_value": 2, "description": "已支付"},
            ],
        })

        with patch("app.knowledge.code_parser.chat_completion", return_value=llm_response):
            enum_classes, index = _parse_enum_classes_batch([enum_file])

        assert len(enum_classes) == 1
        assert enum_classes[0]["enum_class"] == "OrderStatus"
        assert len(enum_classes[0]["values"]) == 2

    def test_dual_key_index(self, tmp_path):
        """enum_class_index 同时用 simple_name 和 fqn 索引"""
        enum_file = self._make_enum_file(
            tmp_path, "PayType.java",
            "package com.shop;\n\npublic enum PayType { WECHAT, ALIPAY; }\n"
        )
        llm_response = json.dumps({
            "enum_class": "PayType",
            "fully_qualified_name": "com.shop.PayType",
            "values": [
                {"name": "WECHAT", "db_value": "WECHAT", "description": None},
                {"name": "ALIPAY", "db_value": "ALIPAY", "description": None},
            ],
        })

        with patch("app.knowledge.code_parser.chat_completion", return_value=llm_response):
            _, index = _parse_enum_classes_batch([enum_file])

        # 双索引都能命中
        assert "PayType" in index
        assert "com.shop.PayType" in index
        assert index["PayType"] is index["com.shop.PayType"]

    def test_handles_llm_error_gracefully(self, tmp_path):
        """LLM 返回非法 JSON → 跳过, 不抛异常"""
        enum_file = self._make_enum_file(
            tmp_path, "Bad.java",
            "package com.x;\n\npublic enum Bad { A; }\n"
        )

        with patch("app.knowledge.code_parser.chat_completion", return_value="not json at all"):
            enum_classes, index = _parse_enum_classes_batch([enum_file])

        assert enum_classes == []
        assert index == {}

    def test_handles_llm_exception(self, tmp_path):
        """LLM 调用抛异常 → 跳过, 不中断其他文件"""
        good_file = self._make_enum_file(
            tmp_path, "Good.java",
            "package com.x;\n\npublic enum Good { X; }\n"
        )
        bad_file = self._make_enum_file(
            tmp_path, "Bad.java",
            "package com.x;\n\npublic enum Bad { Y; }\n"
        )

        call_count = [0]

        def mock_llm(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("LLM timeout")
            return json.dumps({
                "enum_class": "Good",
                "fully_qualified_name": "com.x.Good",
                "values": [{"name": "X", "db_value": "X", "description": None}],
            })

        with patch("app.knowledge.code_parser.chat_completion", side_effect=mock_llm):
            enum_classes, index = _parse_enum_classes_batch([bad_file, good_file])

        assert len(enum_classes) == 1
        assert enum_classes[0]["enum_class"] == "Good"

    def test_skips_empty_values(self, tmp_path):
        """LLM 返回 values 为空数组 → 跳过"""
        enum_file = self._make_enum_file(
            tmp_path, "Empty.java",
            "package com.x;\n\npublic enum Empty {}\n"
        )
        llm_response = json.dumps({
            "enum_class": "Empty",
            "fully_qualified_name": "com.x.Empty",
            "values": [],
        })

        with patch("app.knowledge.code_parser.chat_completion", return_value=llm_response):
            enum_classes, index = _parse_enum_classes_batch([enum_file])

        assert enum_classes == []
        assert index == {}


# ════════════════════════════════════════════
#  _enrich_entity_fields_with_enum_index
# ════════════════════════════════════════════


class TestEnrichEntityFieldsWithEnumIndex:
    """字段 enum_values 注入"""

    @pytest.fixture
    def enum_index(self):
        ed = EnumDef(
            enum_class="OrderStatus",
            fully_qualified_name="com.example.OrderStatus",
            values=[
                EnumValue(name="CREATED", db_value=1, description="已创建"),
                EnumValue(name="PAID", db_value=2, description="已支付"),
            ],
        )
        return {
            "OrderStatus": ed,
            "com.example.OrderStatus": ed,
        }

    def test_enriches_jpa_entity_columns(self, enum_index):
        """JPA entity columns 中 type 匹配 → 注入 enum_values"""
        entities = [{
            "table": "t_order",
            "columns": [
                {"name": "status", "type": "OrderStatus", "column": "status"},
                {"name": "id", "type": "Long", "column": "id"},
            ],
        }]

        enrich_entity_fields_with_enum_index(entities, [], enum_index)

        status_col = entities[0]["columns"][0]
        assert status_col["enum_values"] == [
            {"name": "CREATED", "db_value": 1, "description": "已创建"},
            {"name": "PAID", "db_value": 2, "description": "已支付"},
        ]
        assert status_col["_enum_source"] == "code_type"
        # Long 字段不受影响
        assert "enum_values" not in entities[0]["columns"][1]

    def test_enriches_mongo_doc_fields(self, enum_index):
        """Mongo document fields 中 type 匹配 → 注入 enum_values"""
        mongo_docs = [{
            "collection": "orders",
            "fields": [
                {"field": "status", "type": "OrderStatus", "description": "状态"},
                {"field": "amount", "type": "BigDecimal"},
            ],
        }]

        enrich_entity_fields_with_enum_index([], mongo_docs, enum_index)

        status_field = mongo_docs[0]["fields"][0]
        assert status_field["_enum_source"] == "code_type"
        assert len(status_field["enum_values"]) == 2

    def test_handles_generic_type(self, enum_index):
        """List<OrderStatus> 泛型内层也能匹配"""
        mongo_docs = [{
            "collection": "orders",
            "fields": [
                {"field": "statuses", "type": "List<OrderStatus>"},
            ],
        }]

        enrich_entity_fields_with_enum_index([], mongo_docs, enum_index)

        assert mongo_docs[0]["fields"][0]["_enum_source"] == "code_type_generic"

    def test_enriches_sub_fields(self, enum_index):
        """嵌套 sub_fields 中的 type 也能匹配"""
        mongo_docs = [{
            "collection": "orders",
            "fields": [{
                "field": "detail",
                "type": "OrderDetail",
                "sub_fields": [
                    {"field": "status", "type": "OrderStatus"},
                    {"field": "note", "type": "String"},
                ],
            }],
        }]

        enrich_entity_fields_with_enum_index([], mongo_docs, enum_index)

        sub = mongo_docs[0]["fields"][0]["sub_fields"][0]
        assert sub["_enum_source"] == "code_type"
        assert len(sub["enum_values"]) == 2

    def test_empty_index_noop(self):
        """空 index 不修改任何字段 (但含 enum 后缀的字段标 pending)"""
        entities = [{"columns": [{"name": "x", "type": "OrderStatus"}]}]
        enrich_entity_fields_with_enum_index(entities, [], {})
        assert "enum_values" not in entities[0]["columns"][0]


# ════════════════════════════════════════════
#  enum_extractor module (EnumDef-based API)
# ════════════════════════════════════════════


class TestEnumExtractorModule:
    """Tests for the independent enum_extractor module."""

    def test_scan_enum_classes_delegates(self, tmp_path):
        """scan_enum_classes from enum_extractor works identically."""
        enum_file = tmp_path / "OrderStatus.java"
        enum_file.write_text(
            "package com.example;\n\n"
            "public enum OrderStatus {\n"
            "    CREATED(1, \"已创建\");\n"
            "}\n"
        )
        non_enum = tmp_path / "Order.java"
        non_enum.write_text("package com.example; public class Order {}")

        result = scan_enum_classes([str(enum_file), str(non_enum)])
        assert result == [str(enum_file)]

    def test_build_enum_class_index_dual_key(self):
        """build_enum_class_index indexes by both simple_name and fqn."""
        ed = EnumDef(
            enum_class="OrderStatus",
            fully_qualified_name="com.example.OrderStatus",
            values=[EnumValue(name="CREATED", db_value=1, description="已创建")],
        )
        idx = build_enum_class_index([ed])
        assert idx["OrderStatus"] is ed
        assert idx["com.example.OrderStatus"] is ed

    def test_enrich_entity_fields_with_enum_def(self):
        """enrich_entity_fields_with_enum_index works with EnumDef index."""
        ed = EnumDef(
            enum_class="OrderStatus",
            fully_qualified_name="com.example.OrderStatus",
            values=[
                EnumValue(name="CREATED", db_value=1, description="已创建"),
                EnumValue(name="PAID", db_value=2, description="已支付"),
            ],
        )
        idx = build_enum_class_index([ed])
        entities = [{
            "columns": [
                {"name": "status", "type": "OrderStatus"},
                {"name": "id", "type": "Long"},
            ],
        }]
        enrich_entity_fields_with_enum_index(entities, [], idx)

        status_col = entities[0]["columns"][0]
        assert status_col["enum_values"][0]["name"] == "CREATED"
        assert status_col["_enum_source"] == "code_type"
        assert "enum_values" not in entities[0]["columns"][1]


# ════════════════════════════════════════════
#  _resolve_enum_class (Phase 1 Plan 01 Task 3)
# ════════════════════════════════════════════


@pytest.fixture
def resolve_enum_index():
    return {
        "OrderStatus": EnumDef(
            enum_class="OrderStatus",
            fully_qualified_name="com.x.OrderStatus",
            values=[
                EnumValue(name="CREATED", db_value=1, description="已创建"),
                EnumValue(name="PAID", db_value=2, description="已支付"),
            ],
        ),
        "DeleteStatus": EnumDef(
            enum_class="DeleteStatus",
            fully_qualified_name="com.x.DeleteStatus",
            values=[EnumValue(name="NORMAL", db_value=0, description="正常")],
        ),
        "DeleteStatusEnum": EnumDef(
            enum_class="DeleteStatusEnum",
            fully_qualified_name="com.y.DeleteStatusEnum",
            values=[EnumValue(name="A", db_value=0, description="A")],
        ),
        "ModuleMarkResourceTypeEnum": EnumDef(
            enum_class="ModuleMarkResourceTypeEnum",
            fully_qualified_name="com.x.ModuleMarkResourceTypeEnum",
            values=[EnumValue(name="X", db_value=1, description="X")],
        ),
    }


class TestResolveEnumClass:
    def test_layer1_hint_hit(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "Integer", "field": "status", "enum_class_hint": "OrderStatus"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec == "OrderStatus" and src == "code_hint"

    def test_layer1_hint_with_fqn(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "Integer", "field": "status", "enum_class_hint": "com.x.OrderStatus"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec == "OrderStatus" and src == "code_hint"

    def test_layer1_hint_priority_over_layer2(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        # type 字面也命中, 但 hint 优先
        f = {"type": "OrderStatus", "field": "status", "enum_class_hint": "OrderStatus"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert src == "code_hint"

    def test_layer2_type_hit(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "OrderStatus", "field": "status"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec == "OrderStatus" and src == "code_type"

    def test_layer3_generic_inner(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "List<OrderStatus>", "field": "statuses"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec == "OrderStatus" and src == "code_type_generic"

    def test_layer4_root_match(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "Integer", "field": "orderStatus"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec == "OrderStatus" and src == "name_heuristic"

    def test_layer4_multi_candidate_takes_shortest(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "Integer", "field": "deleteStatus"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec == "DeleteStatus"  # 而非 DeleteStatusEnum

    def test_layer4_root_unequal_misses(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        # moduleType 词根 ['module'] != ModuleMarkResourceType 词根 ['module','mark','resource']
        f = {"type": "Integer", "field": "moduleType"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec is None

    def test_layer4_single_token_field_misses(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "Integer", "field": "type"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec is None

    def test_layer4_unknown_suffix_misses(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        f = {"type": "Integer", "field": "orderName"}  # Name 不在 ENUM_NAME_SUFFIXES
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec is None

    def test_layer4_strong_typed_field_skips_layer4(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        # type 是 enum 类但 enum_index 中没有 → Layer 2 miss → 不进 Layer 4 (因为 type 非基础类型)
        f = {"type": "PaymentStatus", "field": "paymentStatus"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec is None

    def test_layer1_hint_miss_keeps_in_payload(self, resolve_enum_index):
        from app.knowledge.enum_extractor import _resolve_enum_class

        # hint 指向 enum_index 没有的类, layer1 miss, 但其他 layer 也 miss
        f = {"type": "Integer", "field": "isDeleted", "enum_class_hint": "GoneStatus"}
        ec, src = _resolve_enum_class(f, resolve_enum_index)
        assert ec is None



# ════════════════════════════════════════════
#  TestEnrichWritesSourceMetadata (Phase 1 Plan 01 Task 4)
# ════════════════════════════════════════════


class TestEnrichWritesSourceMetadata:
    @pytest.fixture
    def enum_index(self):
        return {
            "OrderStatus": EnumDef(
                enum_class="OrderStatus",
                fully_qualified_name="com.x.OrderStatus",
                values=[
                    EnumValue(name="CREATED", db_value=1, description="已创建"),
                    EnumValue(name="PAID", db_value=2, description="已支付"),
                ],
            ),
            "DeleteStatus": EnumDef(
                enum_class="DeleteStatus",
                fully_qualified_name="com.x.DeleteStatus",
                values=[EnumValue(name="NORMAL", db_value=0, description="正常")],
            ),
            "DeleteStatusEnum": EnumDef(
                enum_class="DeleteStatusEnum",
                fully_qualified_name="com.y.DeleteStatusEnum",
                values=[EnumValue(name="A", db_value=0, description="A")],
            ),
            "ModuleMarkResourceTypeEnum": EnumDef(
                enum_class="ModuleMarkResourceTypeEnum",
                fully_qualified_name="com.x.ModuleMarkResourceTypeEnum",
                values=[EnumValue(name="X", db_value=1, description="X")],
            ),
        }

    def test_layer1_hint_writes_source(self, enum_index):
        entities: list[dict] = []
        mongo_docs = [{
            "class_name": "OrderEntity", "collection": "orders",
            "fields": [{"field": "status", "type": "Integer", "enum_class_hint": "OrderStatus"}],
        }]
        enrich_entity_fields_with_enum_index(entities, mongo_docs, enum_index)
        f = mongo_docs[0]["fields"][0]
        assert f.get("enum_values"), "enum_values 未填"
        assert f.get("_enum_source") == "code_hint"
        assert f.get("_enum_class_name") == "OrderStatus"

    def test_layer4_heuristic_writes_source(self, enum_index):
        entities: list[dict] = []
        mongo_docs = [{
            "class_name": "OrderEntity", "collection": "orders",
            "fields": [{"field": "orderStatus", "type": "Integer"}],
        }]
        enrich_entity_fields_with_enum_index(entities, mongo_docs, enum_index)
        f = mongo_docs[0]["fields"][0]
        assert f.get("_enum_source") == "name_heuristic"
        assert f.get("_enum_class_name") == "OrderStatus"

    def test_pending_status_when_field_has_enum_suffix_but_miss(self, enum_index):
        entities: list[dict] = []
        mongo_docs = [{
            "class_name": "Doc", "collection": "docs",
            "fields": [{"field": "moduleType", "type": "Integer"}],
        }]
        enrich_entity_fields_with_enum_index(entities, mongo_docs, enum_index)
        f = mongo_docs[0]["fields"][0]
        assert "enum_values" not in f
        assert f.get("_enum_match_status") == "pending"

    def test_no_enum_meta_when_no_suffix(self, enum_index):
        entities: list[dict] = []
        mongo_docs = [{
            "class_name": "Doc", "collection": "docs",
            "fields": [{"field": "amount", "type": "Integer"}],
        }]
        enrich_entity_fields_with_enum_index(entities, mongo_docs, enum_index)
        f = mongo_docs[0]["fields"][0]
        assert "_enum_source" not in f
        assert "_enum_match_status" not in f

    def test_does_not_overwrite_existing_enum_values(self, enum_index):
        existing = [{"name": "FROM_META", "db_value": 99, "description": "from_meta"}]
        entities: list[dict] = []
        mongo_docs = [{
            "class_name": "Doc", "collection": "docs",
            "fields": [{
                "field": "status", "type": "Integer",
                "enum_class_hint": "OrderStatus",
                "enum_values": existing,
            }],
        }]
        enrich_entity_fields_with_enum_index(entities, mongo_docs, enum_index)
        f = mongo_docs[0]["fields"][0]
        assert f["enum_values"] == existing
