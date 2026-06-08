"""T7 — promote 路径下 mongodb 字段等价端到端测试 (从原 test_mongo_canonical_merge.py 改写).

设计源: spec 04-implementation-plan.md T7. 验证 mongodb 同字段不同 candidate 走
mongo_struct / 真分歧 写 SchemaCanonicalConflict 的全链路. 这是 promote 路径
取代旧 fragment merge 路径后的回归保障 — 旧路径已删, 新路径必须覆盖等价/冲突两侧.

通用业务字段示例: orders.items / users.preferences (不带客户专属词).
fixtures: tests/knowledge/conftest.py 的 db_session.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.knowledge.equivalence.checkers  # noqa: F401
from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.models import Namespace, SchemaCanonicalConflict, SchemaCanonicalObject

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ns(db_session):
    obj = Namespace(slug="t7_mongo_eq", name="T7 Mongo Equivalence")
    db_session.add(obj)
    await db_session.flush()
    await db_session.refresh(obj)
    return obj


async def _write_mongo_field(db, ns_id, *, field_path, value, source):
    await write_canonical_candidate(
        db, namespace_id=ns_id, db_type="mongodb", database="db1",
        target="orders", field_path=field_path,
        candidate_kind="field_description",
        candidate_value=value,
        evidence_sources=[{"source": source}],
        confidence_status="confirmed_by_code",
    )


class TestMongoFieldStructuralEquivalent:
    """mongodb 嵌套结构等价 → mongo_struct:matched, 不写 conflict."""

    async def test_list_subfield_order_independent(self, db_session, ns):
        """List<OrderItem> 的 sub_fields 顺序不同, 结构等价 → matched."""
        items_a = {
            "type": "List",
            "sub_fields": [
                {"field": "sku", "type": "String"},
                {"field": "qty", "type": "Integer"},
            ],
        }
        items_b = {
            "type": "List",
            "sub_fields": [
                {"field": "qty", "type": "Integer"},
                {"field": "sku", "type": "String"},
            ],
        }
        await _write_mongo_field(db_session, ns.id, field_path="items",
                                  value=items_a, source="repo_a")
        await _write_mongo_field(db_session, ns.id, field_path="items",
                                  value=items_b, source="repo_b")
        await db_session.commit()

        report = await promote_candidates_to_canonical(db_session, ns.id)
        await db_session.commit()
        assert report.promoted_count == 1
        assert report.conflicted_count == 0

        sco = (await db_session.execute(
            select(SchemaCanonicalObject).where(
                SchemaCanonicalObject.namespace_id == ns.id,
                SchemaCanonicalObject.db_type == "mongodb",
                SchemaCanonicalObject.target == "orders",
            )
        )).scalar_one_or_none()
        assert sco is not None


class TestMongoFieldRealConflict:
    """mongodb 字段子结构真冲突 → 写 SchemaCanonicalConflict."""

    async def test_subfield_type_mismatch_writes_conflict(self, db_session, ns):
        """同字段 sub_fields type 不同 (Integer vs String), mongo_struct miss → conflict."""
        # description 都非空且不同 → non_empty_wins miss; sub_fields type 矛盾 →
        # mongo_struct miss; semantic_llm mock false → 全 miss → conflict
        items_a = {
            "type": "List",
            "description": "订单条目, 数量字段为整数",
            "sub_fields": [{"field": "qty", "type": "Integer"}],
        }
        items_b = {
            "type": "List",
            "description": "订单条目, 数量字段为字符串",
            "sub_fields": [{"field": "qty", "type": "String"}],
        }
        await _write_mongo_field(db_session, ns.id, field_path="items",
                                  value=items_a, source="repo_a")
        await _write_mongo_field(db_session, ns.id, field_path="items",
                                  value=items_b, source="repo_b")
        await db_session.commit()

        # mock semantic_llm 返 false 防 LLM 救场
        from unittest.mock import patch
        import json
        fake_response = json.dumps(
            {"equivalent": False, "confidence": 0.9, "reason": "type 矛盾"},
            ensure_ascii=False,
        )
        target_path = "app.knowledge.equivalence.strategies.semantic_llm._call_llm"
        with patch(target_path, return_value=fake_response):
            from app.knowledge.equivalence.strategies.semantic_llm import _SemanticBudget
            _SemanticBudget.reset()
            report = await promote_candidates_to_canonical(db_session, ns.id)
        await db_session.commit()
        assert report.promoted_count == 0
        assert report.conflicted_count == 1

        # SchemaCanonicalConflict 应写入 mongodb 一行
        conflict = (await db_session.execute(
            select(SchemaCanonicalConflict).where(
                SchemaCanonicalConflict.namespace_id == ns.id,
                SchemaCanonicalConflict.db_type == "mongodb",
                SchemaCanonicalConflict.target == "orders",
                SchemaCanonicalConflict.field_path == "items",
            )
        )).scalar_one_or_none()
        assert conflict is not None
        assert conflict.status == "open"
