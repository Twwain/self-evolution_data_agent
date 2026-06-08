"""T2 — mongo_struct strategy 单测.

移植自 mongo_canonical.py:_contents_structurally_equivalent 的回归 case.
4 case: List<X> / Set<X> / Collection<X> / sub_fields 嵌套.
"""

import json

import pytest

from app.knowledge.equivalence.strategies.mongo_struct import mongo_struct_checker


def _make_candidate(value: dict, cand_id: int = 1):
    """构造最小 SchemaCanonicalCandidate mock (field_description kind)."""

    class FakeCandidate:
        def __init__(self, val, id_):
            self.id = id_
            self.candidate_value_json = json.dumps(val, ensure_ascii=False)

    return FakeCandidate(value, cand_id)


class TestMongoStructEquivalent:
    def test_list_generic_same_sub_fields_equivalent(self):
        """List<OrderItem> 两个 repo 解析出相同 sub_fields → 等价."""
        val_a = {
            "type": "List<OrderItem>",
            "sub_fields": [
                {"field": "sku", "type": "String"},
                {"field": "quantity", "type": "Integer"},
            ],
        }
        val_b = {
            "type": "List<OrderItemEntity>",
            "sub_fields": [
                {"field": "sku", "type": "String"},
                {"field": "quantity", "type": "int"},  # int vs Integer → boxing equiv
            ],
        }
        c1 = _make_candidate(val_a, cand_id=1)
        c2 = _make_candidate(val_b, cand_id=2)

        result = mongo_struct_checker([c1, c2])
        assert result is not None
        winner, reason = result
        assert "structural_equivalent" in reason

    def test_set_generic_same_sub_fields_equivalent(self):
        """Set<ImageRef> 两个 repo 解析出相同 sub_fields → 等价."""
        val_a = {
            "type": "Set<ImageRef>",
            "sub_fields": [
                {"field": "url", "type": "String"},
                {"field": "width", "type": "int"},
            ],
        }
        val_b = {
            "type": "Set<ImageReference>",
            "sub_fields": [
                {"field": "url", "type": "String"},
                {"field": "width", "type": "Integer"},
            ],
        }
        c1 = _make_candidate(val_a, cand_id=1)
        c2 = _make_candidate(val_b, cand_id=2)

        result = mongo_struct_checker([c1, c2])
        assert result is not None

    def test_collection_generic_same_sub_fields_equivalent(self):
        """Collection<Tag> 两个 repo 解析出相同 sub_fields → 等价."""
        val_a = {
            "type": "Collection<Tag>",
            "sub_fields": [
                {"field": "name", "type": "String"},
                {"field": "weight", "type": "double"},
            ],
        }
        val_b = {
            "type": "Collection<TagEntity>",
            "sub_fields": [
                {"field": "name", "type": "String"},
                {"field": "weight", "type": "Double"},
            ],
        }
        c1 = _make_candidate(val_a, cand_id=1)
        c2 = _make_candidate(val_b, cand_id=2)

        result = mongo_struct_checker([c1, c2])
        assert result is not None

    def test_nested_sub_fields_equivalent(self):
        """嵌套 sub_fields (二层) 结构等价."""
        val_a = {
            "type": "List<Address>",
            "sub_fields": [
                {"field": "city", "type": "String"},
                {"field": "location", "type": "Object", "sub_fields": [
                    {"field": "lat", "type": "double"},
                    {"field": "lng", "type": "Double"},
                ]},
            ],
        }
        val_b = {
            "type": "List<AddressEntity>",
            "sub_fields": [
                {"field": "city", "type": "String"},
                {"field": "location", "type": "GeoPoint", "sub_fields": [
                    {"field": "lat", "type": "Double"},
                    {"field": "lng", "type": "double"},
                ]},
            ],
        }
        c1 = _make_candidate(val_a, cand_id=1)
        c2 = _make_candidate(val_b, cand_id=2)

        result = mongo_struct_checker([c1, c2])
        assert result is not None


class TestMongoStructNotEquivalent:
    def test_different_field_names_not_equivalent(self):
        """sub_fields 字段名不同 → 不等价, 返 None."""
        val_a = {
            "type": "List<Item>",
            "sub_fields": [
                {"field": "sku", "type": "String"},
                {"field": "price", "type": "Double"},
            ],
        }
        val_b = {
            "type": "List<Item>",
            "sub_fields": [
                {"field": "sku", "type": "String"},
                {"field": "cost", "type": "Double"},
            ],
        }
        c1 = _make_candidate(val_a, cand_id=1)
        c2 = _make_candidate(val_b, cand_id=2)

        result = mongo_struct_checker([c1, c2])
        assert result is None

    def test_no_sub_fields_different_types_not_equivalent(self):
        """无 sub_fields 且类型不同 (非 boxing) → 不等价."""
        val_a = {"type": "String"}
        val_b = {"type": "Integer"}
        c1 = _make_candidate(val_a, cand_id=1)
        c2 = _make_candidate(val_b, cand_id=2)

        result = mongo_struct_checker([c1, c2])
        assert result is None
