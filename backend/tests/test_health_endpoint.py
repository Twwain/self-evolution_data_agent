"""health 端点 — DB 可达性探针 (compose healthcheck 真就绪门)"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.db.metadata import get_db
from app.main import app


@pytest.mark.asyncio
async def test_health_ok_when_db_reachable():
    """DB 可达 → 200 + db=ok。

    经 dependency_overrides[get_db] 注入可控 session:
    execute(SELECT 1) 成功返回 → health 返 200。
    不连真实库, 纯测端点路由逻辑; DB 集成由 Stage 4 验收覆盖。
    """

    class _OkSession:
        async def execute(self, *a, **k):
            return "SELECT 1 ok"

    async def _override():
        yield _OkSession()

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/health")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


@pytest.mark.asyncio
async def test_health_degraded_when_db_down():
    """DB 不可达 → 503 + status=degraded (探针真实失败, 非橡皮图章)。"""

    class _BoomSession:
        async def execute(self, *a, **k):
            raise OSError("connection refused")

    async def _override():
        yield _BoomSession()

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/health")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "db": "down"}
