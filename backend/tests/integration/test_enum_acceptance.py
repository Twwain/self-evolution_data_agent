"""
验收门 G2/G3/G5/G6/G8 集成测试.
测试编号对应 docs/.../06-acceptance.md.
"""
import json
import os
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace
from app.models.schema_canonical_object import SchemaCanonicalObject

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/intelligent_statistics_test",
)

# ════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════


@pytest_asyncio.fixture
async def async_db():
    """独立 PostgreSQL session."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def ns(async_db: AsyncSession):
    """创建测试 namespace."""
    ns = Namespace(slug="acceptance_test", name="Acceptance Test")
    async_db.add(ns)
    await async_db.commit()
    await async_db.refresh(ns)
    return ns


# ════════════════════════════════════════════
#  G2: 启发式精度 — benchmark 脚本退出码
# ════════════════════════════════════════════


@pytest.mark.slow
def test_g2_benchmark_match_rate():
    """G2.1: benchmark 总命中率 >= 22%, 脚本退出码 0."""
    from pathlib import Path

    # 跳过条件: repos 不存在
    repos_base = Path("data/repos")
    if not all((repos_base / str(i)).exists() for i in [3, 5, 6, 7]):
        pytest.skip("data/repos/{3,5,6,7} 未拉到本地")

    r = subprocess.run(
        [sys.executable, "scripts/benchmark_enum_prompt.py"],
        capture_output=True,
        text=True,
        cwd=".",
        timeout=120,
    )
    assert r.returncode == 0, (
        f"benchmark failed (exit={r.returncode}):\n{r.stderr[-500:]}"
    )


# ════════════════════════════════════════════
#  G3: 手动录入闭环 — EnumDictionary 行存在
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_g3_manual_create_enum_dictionary(admin_client, db):
    """G3.1: POST 创建 enum → EnumDictionary 行存在 (source=manual)."""
    from app.models.user import User

    # 确保 admin user 行存在 (FK 约束)
    existing = await db.get(User, 1)
    if not existing:
        db.add(User(id=1, username="admin", role="admin", password_hash="x"))
        await db.commit()

    # 创建 namespace
    ns = Namespace(slug="g3_test", name="G3 Test")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    resp = await admin_client.post(
        "/api/enum-dictionary",
        json={
            "namespace_id": ns.id,
            "enum_class_name": "G3TestEnum",
            "values": [
                {"name": "ACTIVE", "db_value": 1},
                {"name": "INACTIVE", "db_value": 0},
            ],
        },
    )
    assert resp.status_code in (200, 201), f"创建失败: {resp.text}"

    # 验证 GET 返回
    resp2 = await admin_client.get(
        f"/api/enum-dictionary?namespace_id={ns.id}",
    )
    assert resp2.status_code == 200
    items = resp2.json().get("items", resp2.json().get("data", []))
    names = [it.get("enum_class_name") for it in items]
    assert "G3TestEnum" in names


# ════════════════════════════════════════════
#  G5: 反向同步 — create 事件 rebind pending 字段
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_g5_create_event_rebinds_pending(async_db: AsyncSession, ns: Namespace):
    """G5.1: 新建 EnumDictionary → pending 字段自动 matched."""
    sco = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db1",
        target="orders",
        fields_json=json.dumps([{
            "name": "orderStatus",
            "type": "Integer",
            "enum_class_hint": "OrderStatus",
            "enum_match_status": "pending",
        }]),
    )
    e = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="OrderStatus",
        values_json='[{"name":"CREATED","db_value":1},{"name":"PAID","db_value":2}]',
        source="manual",
    )
    async_db.add_all([sco, e])
    await async_db.commit()
    await async_db.refresh(e)

    from app.knowledge.enum_sync import sync_enum_dict_to_bound_fields

    report = await sync_enum_dict_to_bound_fields(async_db, e.id, event="create")
    assert report["rebound"] >= 1

    await async_db.refresh(sco)
    fields = json.loads(sco.fields_json)
    f = next(x for x in fields if x["name"] == "orderStatus")
    assert f["enum_match_status"] == "matched"
    assert f["enum_ref_id"] == e.id
    assert f["enum_values"] == [
        {"name": "CREATED", "db_value": 1},
        {"name": "PAID", "db_value": 2},
    ]


# ════════════════════════════════════════════
#  G6: fetch_schema 输出契约 — _filter_enum_fields_for_llm
# ════════════════════════════════════════════


def test_g6_matched_returns_enum_values():
    """G6.1: matched 字段保留 enum_values."""
    from app.engine.tools.data_access_tools import _filter_enum_fields_for_llm

    field = {
        "name": "status",
        "type": "Integer",
        "enum_values": [{"name": "A", "db_value": 1}],
        "enum_match_status": "matched",
        "enum_ref_id": 42,
        "enum_source": "code_hint",
    }
    out = _filter_enum_fields_for_llm(field)
    assert out["enum_values"] == [{"name": "A", "db_value": 1}]
    # 内部字段不外泄
    assert "enum_ref_id" not in out
    assert "enum_source" not in out


def test_g6_pending_strips_enum_values():
    """G6.2: pending 字段不返回 enum_values."""
    from app.engine.tools.data_access_tools import _filter_enum_fields_for_llm

    field = {
        "name": "isDeleted",
        "type": "Integer",
        "enum_values": [{"name": "A", "db_value": 1}],
        "enum_match_status": "pending",
        "enum_ref_id": 10,
    }
    out = _filter_enum_fields_for_llm(field)
    assert "enum_values" not in out
    assert "enum_ref_id" not in out


def test_g6_conflict_strips_enum_values():
    """G6.3: conflict 字段不返回 enum_values."""
    from app.engine.tools.data_access_tools import _filter_enum_fields_for_llm

    field = {
        "name": "type",
        "type": "Integer",
        "enum_values": [{"name": "B", "db_value": 2}],
        "enum_match_status": "conflict",
    }
    out = _filter_enum_fields_for_llm(field)
    assert "enum_values" not in out


# ════════════════════════════════════════════
#  G8: 安全 — check_no_hardcode 通过
# ════════════════════════════════════════════


def test_g8_check_no_hardcode():
    """G8: scripts/check_no_hardcode.py 退出码 0."""
    r = subprocess.run(
        [sys.executable, "-m", "scripts.check_no_hardcode"],
        capture_output=True,
        text=True,
        cwd=".",
        timeout=60,
    )
    assert r.returncode == 0, (
        f"check_no_hardcode failed:\n{r.stdout[-500:]}\n{r.stderr[-500:]}"
    )
