"""Stage 4 Task 5 — prequery_collection + prequery_with_field_extraction tests.

用 patch + AsyncMock 模拟 Motor (与 probe_tools 同模式, 真 Mongo 在 CI 代价高).
"""
from unittest.mock import AsyncMock, patch

import pytest

from .conftest import make_fake_mongo_db


@pytest.mark.asyncio
async def test_prequery_collection_returns_candidates_ok():
    from app.engine.tools.prequery_tools import prequery_collection

    docs = [
        {"_id": "b1", "name": "优选系列"},
        {"_id": "b2", "name": "优选一起"},
    ]
    fake_db = make_fake_mongo_db({"c_category": docs})

    with patch("app.engine.tools.prequery_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await prequery_collection(
            namespace_id=1, database="t", collection="c_category",
            fields=["name"], pattern="优选",
        )
        assert out["status"] == "ok"
        assert out["total"] == 2
        assert len(out["candidates"]) == 2
        assert out["candidates"][0]["value"] == "b1"
        assert out["candidates"][0]["label"] == "优选系列"


@pytest.mark.asyncio
async def test_prequery_collection_zero_hit():
    from app.engine.tools.prequery_tools import prequery_collection
    fake_db = make_fake_mongo_db({"c_category": []})
    with patch("app.engine.tools.prequery_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await prequery_collection(
            namespace_id=1, database="t", collection="c_category",
            fields=["name"], pattern="不存在的",
        )
        assert out["status"] == "zero_hit"
        assert out["total"] == 0
        assert out["candidates"] == []


@pytest.mark.asyncio
async def test_prequery_collection_overflow(monkeypatch):
    """超过 settings.prequery_overflow_threshold → status=overflow, candidates 截断."""
    from app.engine.tools.prequery_tools import prequery_collection
    from app.engine.tools import prequery_tools
    monkeypatch.setattr(prequery_tools.settings, "prequery_overflow_threshold", 3)

    docs = [{"_id": f"b{i}", "name": f"name{i}"} for i in range(10)]
    fake_db = make_fake_mongo_db({"c_category": docs})

    with patch("app.engine.tools.prequery_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await prequery_collection(
            namespace_id=1, database="t", collection="c_category",
            fields=["name"], pattern="name",
        )
        assert out["status"] == "overflow"
        assert out["total"] >= 4  # 至少多拉 1 条判断
        assert len(out["candidates"]) == 3  # 截断到阈值


@pytest.mark.asyncio
async def test_prequery_collection_uses_case_insensitive_regex():
    """M1: regex 必须带 $options=i, 大小写不敏感命中."""
    from app.engine.tools.prequery_tools import prequery_collection

    fake_db = make_fake_mongo_db({"c_category": [{"_id": "b1", "name": "X"}]})
    captured: dict = {}

    def _get_coll(name):
        from unittest.mock import MagicMock
        from .conftest import FakeMongoCursor
        coll = MagicMock()

        def _find(query):
            captured["query"] = query
            return FakeMongoCursor([{"_id": "b1", "name": "X"}])

        coll.find.side_effect = _find
        return coll

    fake_db.__getitem__.side_effect = _get_coll

    with patch("app.engine.tools.prequery_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        await prequery_collection(
            namespace_id=1, database="t", collection="c_category",
            fields=["name", "title"], pattern="abc",
        )
    # 每个分支必须含 $options: "i"
    branches = captured["query"]["$or"]
    assert all(list(b.values())[0]["$options"] == "i" for b in branches)


@pytest.mark.asyncio
async def test_prequery_with_field_extraction_chains_upstream_to_downstream():
    from app.engine.tools.prequery_tools import prequery_with_field_extraction

    upstream_docs = [
        {"_id": "b1", "name": "优选系列"},
        {"_id": "b2", "name": "优选一起"},
    ]
    downstream_docs = [
        {"_id": "p1", "categoryId": "b1", "name": "订单 1"},
        {"_id": "p2", "categoryId": "b1", "name": "订单 2"},
        {"_id": "p3", "categoryId": "b2", "name": "订单 3"},
    ]
    fake_db = make_fake_mongo_db({
        "c_category": upstream_docs,
        "c_product": downstream_docs,
    })

    with patch("app.engine.tools.prequery_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await prequery_with_field_extraction(
            namespace_id=1, database="t",
            upstream_coll="c_category", extract_field="_id",
            upstream_fields=["name"], upstream_pattern="优选",
            downstream_coll="c_product", downstream_filter_field="categoryId",
        )
        assert out["status"] == "ok"
        assert out["upstream_count"] == 2
        assert out["upstream_ids"] == ["b1", "b2"]
        assert out["total"] == 3
        assert {c["value"] for c in out["candidates"]} == {"p1", "p2", "p3"}


@pytest.mark.asyncio
async def test_prequery_with_field_extraction_zero_upstream():
    """上游零命中 → 短路, 不查下游, status='zero_hit_upstream'."""
    from app.engine.tools.prequery_tools import prequery_with_field_extraction
    fake_db = make_fake_mongo_db({"c_category": []})
    with patch("app.engine.tools.prequery_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await prequery_with_field_extraction(
            namespace_id=1, database="t",
            upstream_coll="c_category", extract_field="_id",
            upstream_fields=["name"], upstream_pattern="不存在",
            downstream_coll="c_product", downstream_filter_field="categoryId",
        )
        assert out["status"] == "zero_hit_upstream"
        assert out["upstream_count"] == 0
        assert out["candidates"] == []


@pytest.mark.asyncio
async def test_prequery_with_field_extraction_label_field_kept_in_meta():
    """I2: extract_field=name (label 类) 时, 上游 candidate.meta 必须保留 name,
    否则 BL-05 退化抽 _id, 链路彻底失语."""
    from app.engine.tools.prequery_tools import prequery_with_field_extraction

    upstream_docs = [
        {"_id": "u1", "name": "alpha", "extra": "x"},
        {"_id": "u2", "name": "alpha", "extra": "y"},  # 同 name 测 dedup
        {"_id": "u3", "name": "beta", "extra": "z"},
    ]
    downstream_docs = [
        {"_id": "d1", "category": "alpha"},
        {"_id": "d2", "category": "beta"},
    ]
    fake_db = make_fake_mongo_db({
        "c_up": upstream_docs,
        "c_down": downstream_docs,
    })
    with patch("app.engine.tools.prequery_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await prequery_with_field_extraction(
            namespace_id=1, database="t",
            upstream_coll="c_up", extract_field="name",
            upstream_fields=["extra"], upstream_pattern=".",
            downstream_coll="c_down", downstream_filter_field="category",
        )
    # M2: dedup + I2: 真正抽到 name (而非 fallback 到 _id)
    assert out["upstream_ids"] == ["alpha", "beta"]
    assert out["status"] == "ok"
    assert {c["value"] for c in out["candidates"]} == {"d1", "d2"}
