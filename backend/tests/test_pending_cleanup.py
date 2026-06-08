"""
Pending cleanup 测试 — TTL 清理 / 循环异常兜底 / cancel 退出.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.engine.pending_cleanup import _cleanup_once, pending_cleanup_loop
from app.models import Namespace, PendingClarification


# ══════════════════════════════════════════════════════════════════════════════
#  fixtures
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def _session_ctx(session):
    yield session


def _patched_session(session):
    def _factory():
        return _session_ctx(session)
    return _factory


async def _mk_ns(db) -> int:
    ns = Namespace(name="ns-t", slug="ns-t", description="")
    db.add(ns)
    await db.commit()
    return ns.id


async def _mk_pending(db, ns_id: int, *, expired: bool, status: str = "pending") -> int:
    now = datetime.now()
    expires = now - timedelta(hours=1) if expired else now + timedelta(hours=1)
    row = PendingClarification(
        session_id="s1",
        namespace_id=ns_id,
        original_question="q",
        targets_json="[]",
        conditions_json="[]",
        resolved_json="{}",
        pending_cond_ids_json="[]",
        clarification_questions_json="[]",
        status=status,
        expires_at=expires,
    )
    db.add(row)
    await db.commit()
    return row.id


# ══════════════════════════════════════════════════════════════════════════════
#  _cleanup_once
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cleanup_deletes_only_expired(db):
    """过期的删, 未过期的留."""
    ns_id = await _mk_ns(db)
    expired_id = await _mk_pending(db, ns_id, expired=True)
    fresh_id = await _mk_pending(db, ns_id, expired=False)

    with patch("app.engine.pending_cleanup.async_session", _patched_session(db)):
        deleted = await _cleanup_once()

    assert deleted == 1
    remaining = (await db.execute(select(PendingClarification.id))).scalars().all()
    assert expired_id not in remaining
    assert fresh_id in remaining


@pytest.mark.asyncio
async def test_cleanup_deletes_regardless_of_status(db):
    """过期清理不看 status — pending/resolved/abandoned/overflow 都清."""
    ns_id = await _mk_ns(db)
    for st in ("pending", "resolved", "abandoned", "overflow"):
        await _mk_pending(db, ns_id, expired=True, status=st)

    with patch("app.engine.pending_cleanup.async_session", _patched_session(db)):
        deleted = await _cleanup_once()

    assert deleted == 4
    cnt = (await db.execute(select(PendingClarification.id))).scalars().all()
    assert cnt == []


@pytest.mark.asyncio
async def test_cleanup_no_match_returns_zero(db):
    """无过期条目 — 返回 0, 不 raise."""
    ns_id = await _mk_ns(db)
    await _mk_pending(db, ns_id, expired=False)

    with patch("app.engine.pending_cleanup.async_session", _patched_session(db)):
        deleted = await _cleanup_once()

    assert deleted == 0


# ══════════════════════════════════════════════════════════════════════════════
#  pending_cleanup_loop
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_loop_cancelled_exits_cleanly():
    """收到 CancelledError 直接退出, 不抛异常给上层."""
    async def _fake_cleanup():
        return 0

    with patch(
        "app.engine.pending_cleanup._cleanup_once", side_effect=_fake_cleanup,
    ):
        task = asyncio.create_task(pending_cleanup_loop(interval_secs=10))
        await asyncio.sleep(0.05)  # 让循环跑一圈进入 sleep
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_loop_swallows_exceptions_and_continues():
    """_cleanup_once 抛异常, 循环应 log+continue, 不 die."""
    call_count = {"n": 0}
    _real_sleep = asyncio.sleep  # 保真, 避开被 patch 的自引用

    async def _flaky_cleanup():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("db gone")
        return 0

    async def _yield_sleep(_secs):
        _ = _secs
        await _real_sleep(0)

    with patch(
        "app.engine.pending_cleanup._cleanup_once", side_effect=_flaky_cleanup,
    ), patch(
        "app.engine.pending_cleanup.asyncio.sleep", new=_yield_sleep,
    ):
        task = asyncio.create_task(pending_cleanup_loop(interval_secs=0))
        await _real_sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count["n"] >= 2  # 第一次抛异常后仍有后续轮次
