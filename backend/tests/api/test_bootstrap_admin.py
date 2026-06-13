"""bootstrap 默认账号应为 super_admin + 配置化密码。"""
import pytest
from sqlalchemy import select
from app.models.user import User


@pytest.mark.asyncio
async def test_init_admin_creates_super_admin(db, monkeypatch):
    import app.main as m
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session():
        yield db

    monkeypatch.setattr(m, "async_session", _fake_session)
    await m._init_admin()
    row = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
    assert row.role == "super_admin"
