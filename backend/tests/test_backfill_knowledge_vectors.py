"""
Task 3.3 — 历史 KnowledgeEntry 回填向量索引测试
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════
#  辅助构造 — 用 SimpleNamespace 替代 SQLAlchemy 模型
# ════════════════════════════════════════════

from types import SimpleNamespace


def _ns(id: int, slug: str) -> SimpleNamespace:
    return SimpleNamespace(id=id, slug=slug)


def _entry(id: int, content: str, tier: str = "normal",
           entry_type: str = "rule", ns_id: int | None = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=id, content=content, tier=tier, entry_type=entry_type,
        namespace_id=ns_id, is_superseded=False, status="canonical",
        payload="{}",
    )


# ════════════════════════════════════════════
#  测试用例
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_backfill_upserts_namespace_entries():
    """正常回填: 命名空间知识写入向量索引"""
    ns_list = [_ns(1, "demo")]
    entry_list = [_entry(10, "业务术语A", ns_id=1), _entry(11, "业务术语B", ns_id=1)]

    upserted: list[dict] = []

    def fake_upsert(**kwargs):
        upserted.append(kwargs)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[
        MagicMock(scalars=lambda: MagicMock(all=lambda: ns_list)),
        MagicMock(scalars=lambda: MagicMock(all=lambda: entry_list)),
    ])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("scripts.backfill_knowledge_vectors.async_session", return_value=mock_ctx), \
         patch("scripts.backfill_knowledge_vectors.upsert_knowledge_entry", side_effect=fake_upsert):
        from scripts.backfill_knowledge_vectors import backfill
        stats = await backfill(dry_run=False)

    assert stats["total"] == 2
    assert stats["upserted"] == 2
    assert stats["errors"] == 0
    assert all(u["slug"] == "demo" for u in upserted)


@pytest.mark.asyncio
async def test_backfill_global_entries_use_empty_slug():
    """全局知识 (namespace_id=None) → upsert 时 slug=''"""
    ns_list: list = []
    entry_list = [_entry(20, "全局规则", ns_id=None)]

    upserted: list[dict] = []

    def fake_upsert(**kwargs):
        upserted.append(kwargs)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[
        MagicMock(scalars=lambda: MagicMock(all=lambda: ns_list)),
        MagicMock(scalars=lambda: MagicMock(all=lambda: entry_list)),
    ])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("scripts.backfill_knowledge_vectors.async_session", return_value=mock_ctx), \
         patch("scripts.backfill_knowledge_vectors.upsert_knowledge_entry", side_effect=fake_upsert):
        from scripts.backfill_knowledge_vectors import backfill
        stats = await backfill(dry_run=False)

    assert stats["upserted"] == 1
    assert upserted[0]["slug"] == ""
    assert upserted[0]["namespace_id"] is None


@pytest.mark.asyncio
async def test_backfill_dry_run_skips_upsert():
    """dry-run 模式: 不调用 upsert, 但 upserted 计数正确"""
    ns_list = [_ns(1, "demo")]
    entry_list = [_entry(30, "条目C", ns_id=1)]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[
        MagicMock(scalars=lambda: MagicMock(all=lambda: ns_list)),
        MagicMock(scalars=lambda: MagicMock(all=lambda: entry_list)),
    ])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_upsert = MagicMock()
    with patch("scripts.backfill_knowledge_vectors.async_session", return_value=mock_ctx), \
         patch("scripts.backfill_knowledge_vectors.upsert_knowledge_entry", mock_upsert):
        from scripts.backfill_knowledge_vectors import backfill
        stats = await backfill(dry_run=True)

    assert stats["upserted"] == 1   # dry-run 也计数
    mock_upsert.assert_not_called() # 但不真正写入


@pytest.mark.asyncio
async def test_backfill_errors_counted_but_continue():
    """单条 upsert 失败时计入 errors, 继续处理其余条目"""
    ns_list = [_ns(1, "demo")]
    entry_list = [_entry(40, "A", ns_id=1), _entry(41, "B", ns_id=1)]

    call_count = 0

    def flaky_upsert(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("chroma down")

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[
        MagicMock(scalars=lambda: MagicMock(all=lambda: ns_list)),
        MagicMock(scalars=lambda: MagicMock(all=lambda: entry_list)),
    ])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("scripts.backfill_knowledge_vectors.async_session", return_value=mock_ctx), \
         patch("scripts.backfill_knowledge_vectors.upsert_knowledge_entry", side_effect=flaky_upsert):
        from scripts.backfill_knowledge_vectors import backfill
        stats = await backfill(dry_run=False)

    assert stats["errors"] == 1
    assert stats["upserted"] == 1
    assert stats["total"] == 2


@pytest.mark.asyncio
async def test_backfill_ns_filter_includes_global_entries():
    """--ns 过滤指定 namespace 时，namespace_id IS NULL 的全局条目也必须被回填"""
    ns_list = [_ns(1, "demo")]
    # entry 50: 属于 demo namespace, entry 51: 全局 (namespace_id=None)
    entry_list = [_entry(50, "demo 术语", ns_id=1), _entry(51, "全局规则", ns_id=None)]

    upserted: list[dict] = []

    def fake_upsert(**kwargs):
        upserted.append(kwargs)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[
        MagicMock(scalars=lambda: MagicMock(all=lambda: ns_list)),
        MagicMock(scalars=lambda: MagicMock(all=lambda: entry_list)),
    ])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("scripts.backfill_knowledge_vectors.async_session", return_value=mock_ctx), \
         patch("scripts.backfill_knowledge_vectors.upsert_knowledge_entry", side_effect=fake_upsert):
        from scripts.backfill_knowledge_vectors import backfill
        stats = await backfill(dry_run=False, ns_slug_filter="demo")

    assert stats["total"] == 2
    assert stats["upserted"] == 2
    slugs = {u["slug"] for u in upserted}
    # demo 条目用 "demo" slug，全局条目用 "" slug
    assert "demo" in slugs
    assert "" in slugs
