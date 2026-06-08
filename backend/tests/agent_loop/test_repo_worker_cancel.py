"""Stage 4 Task 11 — repo_worker cancel 行为回归.

repo_worker 已具备完整 cancel 链路 (`_active_workers` 注册 + cancel_worker + 内部 finally 清理),
本测试锁住语义不变量, 防止后续重构破坏:

1. cancel_worker(known_id) → task.cancel(), worker 在 1s 内终止
2. cancel_worker(unknown_id) → 静默返回 False (不抛错)
3. cancel 后 _active_workers 注册表自动清理
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.engine.repo_worker import _active_workers, cancel_worker

# ════════════════════════════════════════════
#  1. cancel_worker 触发 CancelledError 在 1s 内完成
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_worker_stops_within_1s():
    cancelled_seen: list[bool] = []

    async def fake_long_worker():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled_seen.append(True)
            raise

    task = asyncio.create_task(fake_long_worker())
    _active_workers["test-cancel-id"] = task
    await asyncio.sleep(0.05)  # 让 worker 起头进入 sleep

    t0 = time.monotonic()
    result = cancel_worker("test-cancel-id")
    assert result is True

    # 等 task 真正终结 (CancelledError) — 但限时 1.5s 防卡死
    try:
        await asyncio.wait_for(task, timeout=1.5)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"cancel 耗时 {elapsed:.2f}s, 应 < 1s"
    assert cancelled_seen == [True], "worker 内部应感知 CancelledError"
    assert "test-cancel-id" not in _active_workers, "cancel_worker 应同步清注册表"


# ════════════════════════════════════════════
#  2. cancel_worker 未知 id → False, 不抛错
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_unknown_worker_returns_false():
    result = cancel_worker("does-not-exist-zzz")
    assert result is False


# ════════════════════════════════════════════
#  3. cancel_worker 已 done 任务 → False (幂等)
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_already_done_worker_returns_false():
    async def quick():
        return "done"

    task = asyncio.create_task(quick())
    await task  # 等其完成
    _active_workers["already-done"] = task

    result = cancel_worker("already-done")
    assert result is False, "已 done 的 task 不应再触发 cancel"

    # 清理测试痕迹
    _active_workers.pop("already-done", None)
