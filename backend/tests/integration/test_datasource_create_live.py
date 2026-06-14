"""集成测 — add_datasource 连库验证 + 画像合成 (双路径). 读 E2E_*_* 凭据.

需求 #1 核心覆盖: 连通才存 + db_profile 填充 + 降级。无凭据时 skip。
"""
from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import select

from app.models import DataSource, Namespace

pytestmark = pytest.mark.live


def _mysql_env() -> dict | None:
    if not os.environ.get("E2E_MYSQL_HOST"):
        return None
    return {
        "db_type": "mysql",
        "host": os.environ["E2E_MYSQL_HOST"],
        "port": int(os.environ.get("E2E_MYSQL_PORT", "3306")),
        "database": os.environ.get("E2E_MYSQL_DB", ""),
        "username": os.environ.get("E2E_MYSQL_USER", ""),
        "password": os.environ.get("E2E_MYSQL_PASS", ""),
        "description": "集成测建源",
    }


def _mongo_env() -> dict | None:
    if not os.environ.get("E2E_MONGO_HOST"):
        return None
    return {
        "db_type": "mongodb",
        "host": os.environ["E2E_MONGO_HOST"],
        "port": int(os.environ.get("E2E_MONGO_PORT", "27017")),
        "database": os.environ.get("E2E_MONGO_DB", ""),
        "username": os.environ.get("E2E_MONGO_USER", ""),
        "password": os.environ.get("E2E_MONGO_PASS", ""),
        "description": "集成测建源",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("env_factory", [_mysql_env, _mongo_env], ids=["mysql", "mongodb"])
async def test_create_live_connects_and_profiles(make_client, db, env_factory):
    """连真实库 → 201 + db_profile 含 version/object_count/profiled_at."""
    body = env_factory()
    if body is None:
        pytest.skip("凭据未配置")
    ns = Namespace(name=f"t-live-{body['db_type']}", slug=f"t-live-{body['db_type']}")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    resp = await client.post(f"/api/namespaces/{ns.id}/datasources", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["description"] == "集成测建源"
    profile = data["db_profile"]
    assert profile.get("version")
    assert isinstance(profile.get("object_count"), int)
    assert profile.get("profiled_at")

    # 落库的 db_profile_json 可解析回同样内容
    ds = (await db.execute(
        select(DataSource).where(DataSource.namespace_id == ns.id)
    )).scalar_one()
    assert json.loads(ds.db_profile_json)["version"] == profile["version"]


@pytest.mark.asyncio
async def test_create_wrong_password_rejects(make_client, db):
    """凭据错误 (连得上 host 但密码错) → 400 不落库. 仅 MySQL 路径 (有 host 时跑)."""
    body = _mysql_env()
    if body is None:
        pytest.skip("E2E_MYSQL_HOST 未配置")
    body = {**body, "password": "definitely_wrong_password_xyz"}
    ns = Namespace(name="t-live-badpwd", slug="t-live-badpwd")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    resp = await client.post(f"/api/namespaces/{ns.id}/datasources", json=body)
    assert resp.status_code == 400, resp.text
    rows = (await db.execute(
        select(DataSource).where(DataSource.namespace_id == ns.id)
    )).scalars().all()
    assert len(rows) == 0
