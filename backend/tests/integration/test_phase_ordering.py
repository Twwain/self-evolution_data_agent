"""Phase 5 Task 5.4: phase 1 闸门未就位时 relabel 拒绝运行.

验证 ``relabel_legacy_terminology._is_phase1_gate_ready`` 闸门:
- gate False → main 抛 RuntimeError("Phase 1 gate not ready")
- gate True  → main 走完空候选库 (SQL 返 [], 主循环零次, 正常 return 0)
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_relabel_blocks_when_gate_not_ready(monkeypatch):
    from scripts import relabel_legacy_terminology as relabel

    async def fake_gate_check(db):
        return False

    monkeypatch.setattr(relabel, "_is_phase1_gate_ready", fake_gate_check)

    with pytest.raises(RuntimeError, match="Phase 1 gate"):
        await relabel.main()


@pytest.mark.asyncio
async def test_relabel_proceeds_when_gate_ready(monkeypatch):
    """Gate ready → main 进入主体. 用 stub session/SQL 让候选库空, 主循环零次."""
    from scripts import relabel_legacy_terminology as relabel

    async def fake_gate_check(db):
        return True

    monkeypatch.setattr(relabel, "_is_phase1_gate_ready", fake_gate_check)

    # ── stub async_session 上下文管理器, db.execute 返空 SQL 结果 ────
    class _StubResult:
        def all(self):
            return []

        def scalar_one_or_none(self):
            return None

    class _StubDB:
        async def execute(self, *args, **kwargs):
            return _StubResult()

        async def commit(self):
            return None

        async def get(self, *args, **kwargs):
            return None

    class _StubCtx:
        async def __aenter__(self):
            return _StubDB()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(relabel, "async_session", lambda: _StubCtx())

    rc = await relabel.main()
    assert rc == 0
