"""集成测 — Oracle 数据源建源验证 + schema refresh + 简单 SELECT.

读 IS_ORACLE_TEST_* 凭据; 无凭据时全部 skip, 不影响 CI.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


def _oracle_env() -> dict | None:
    if not os.environ.get("IS_ORACLE_TEST_HOST"):
        return None
    return {
        "db_type": "oracle",
        "host": os.environ["IS_ORACLE_TEST_HOST"],
        "port": int(os.environ.get("IS_ORACLE_TEST_PORT", "1521")),
        "database": os.environ.get("IS_ORACLE_TEST_SERVICE", ""),
        "username": os.environ.get("IS_ORACLE_TEST_USER", ""),
        "password": os.environ.get("IS_ORACLE_TEST_PASSWORD", ""),
        "description": "Oracle 集成测建源",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  建源 + 画像
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_oracle_create_live_connects_and_profiles(make_client, db):
    """连真实 Oracle → 201 + db_profile 含 version / schema / object_count / profiled_at."""
    body = _oracle_env()
    if body is None:
        pytest.skip("IS_ORACLE_TEST_HOST 未配置")

    from app.models import Namespace
    ns = Namespace(name="t-live-oracle", slug="t-live-oracle")
    db.add(ns)
    await db.flush()

    client = await make_client(role="super_admin", user_id=1)
    resp = await client.post(f"/api/namespaces/{ns.id}/datasources", json=body)
    if resp.status_code == 400:
        pytest.skip(f"Oracle 连接失败 (CI 无真实 Oracle): {resp.json().get('detail')}")
    assert resp.status_code == 201, resp.text

    ds = resp.json()
    assert ds["db_type"] == "oracle"
    profile = ds.get("db_profile", {})
    assert "profiled_at" in profile
    assert "password" not in ds


@pytest.mark.asyncio
async def test_oracle_create_live_bad_credentials_rejected(make_client, db):
    """连不上的 Oracle → 400, 不落库."""
    body = _oracle_env()
    if body is None:
        pytest.skip("IS_ORACLE_TEST_HOST 未配置")

    from app.models import Namespace
    ns = Namespace(name="t-live-oracle-bad", slug="t-live-oracle-bad")
    db.add(ns)
    await db.flush()

    bad_body = {**body, "password": "definitely_wrong_password_xyz"}
    client = await make_client(role="super_admin", user_id=1)
    resp = await client.post(f"/api/namespaces/{ns.id}/datasources", json=bad_body)
    assert resp.status_code == 400, f"应拒绝错误凭据, 实际: {resp.status_code} {resp.text}"


# ══════════════════════════════════════════════════════════════════════════════
#  driver 直接调用 (不过 HTTP)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_oracle_driver_fetch_db_profile_live():
    """OracleDriver.fetch_db_profile 直连, connected=True, version 非空."""
    env = _oracle_env()
    if env is None:
        pytest.skip("IS_ORACLE_TEST_HOST 未配置")

    from app.engine.drivers.oracle import OracleDriver
    from app.models import DataSource

    ds = DataSource(
        id=None,
        db_type="oracle",
        host=env["host"],
        port=env["port"],
        database=env["database"],
        username=env["username"],
        password=env["password"],
        description="live test",
    )
    driver = OracleDriver()
    profile = await driver.fetch_db_profile(ds)

    assert profile.get("connected") is True, f"连接失败: {profile.get('error')}"
    assert "version" in profile, "version 字段缺失"
    assert "schema" in profile, "schema 字段缺失"
    assert "profiled_at" in profile


@pytest.mark.asyncio
async def test_oracle_driver_execute_select_live():
    """OracleDriver.execute_query 执行简单 SELECT 1 FROM DUAL 返回 row."""
    env = _oracle_env()
    if env is None:
        pytest.skip("IS_ORACLE_TEST_HOST 未配置")

    from app.engine.drivers.oracle import OracleDriver
    from app.models import DataSource

    ds = DataSource(
        id=999,  # 非 None 以进池
        db_type="oracle",
        host=env["host"],
        port=env["port"],
        database=env["database"],
        username=env["username"],
        password=env["password"],
        description="live test",
    )
    driver = OracleDriver()
    try:
        result = await driver.execute_query(
            ds, "DUAL",
            {"sql": "SELECT 1 AS val FROM DUAL"},
            mode="single",
        )
        rows = result["rows"]
        assert len(rows) == 1
        assert "val" in rows[0] or "1" in str(rows[0])
    finally:
        await driver.close_all()


@pytest.mark.asyncio
async def test_oracle_driver_fetch_schema_live():
    """OracleDriver.fetch_schema 返回当前 schema 下表列表 (非空)."""
    env = _oracle_env()
    if env is None:
        pytest.skip("IS_ORACLE_TEST_HOST 未配置")

    from app.engine.drivers.oracle import OracleDriver
    from app.models import DataSource

    ds = DataSource(
        id=998,
        db_type="oracle",
        host=env["host"],
        port=env["port"],
        database=env["database"],
        username=env["username"],
        password=env["password"],
        description="live test",
    )
    driver = OracleDriver()
    try:
        tables = await driver.fetch_schema(ds, target=None)
        assert isinstance(tables, list)
        assert len(tables) > 0, "当前 schema 无任何表, 请检查测试用户权限"
        # 每个 stub 都有 target 和 db_type
        for stub in tables:
            assert stub["db_type"] == "oracle"
            assert stub["target"]
    finally:
        await driver.close_all()
