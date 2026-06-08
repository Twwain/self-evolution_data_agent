"""Phase 3 Task 3.1 — namespace 联动 API (databases + collections) 回归.

覆盖 4 用例:
  1. /databases 列出 ns 下所有 DataSource (ds_id / db_type / database / host)
  2. /collections?database=X 对 mongodb DataSource 走 SchemaCanonicalObject 表
  3. /collections?database=未知 返 db_type=null + collections=[]
  4. /collections 缺 database 查询参数 → FastAPI 422
"""

import pytest
import pytest_asyncio

from app.auth import get_current_user
from app.main import app
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.namespace import DataSource, Namespace
from app.models.user import User


# Phase 3 Task 3.1 lookup endpoints 用 get_current_user (非 require_admin),
# 全局 admin_client 仅 override require_admin, 这里补 get_current_user 旁路.
@pytest.fixture(autouse=True)
def _override_current_user():
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="admin", role="admin", password_hash="x",
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════

@pytest_asyncio.fixture
async def seeded_ns_with_3_ds(db) -> int:
    """1 ns + 3 DataSource (mongodb db_q / mysql db_main / mongodb db_log)."""
    ns = Namespace(name="lookup_ns", slug="lookup_ns", description="3.1")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    for db_type, dbname in [
        ("mongodb", "db_q"),
        ("mysql", "db_main"),
        ("mongodb", "db_log"),
    ]:
        db.add(DataSource(
            namespace_id=ns.id,
            db_type=db_type,
            database=dbname,
            host="localhost",
            port=27017 if db_type == "mongodb" else 3306,
            username="",
            password="",
        ))
    await db.commit()
    return ns.id


@pytest_asyncio.fixture
async def seeded_ns_with_canonicals(db) -> int:
    """1 ns + 1 mongodb DataSource(database=db_q) — collection 由下一个 fixture 注入."""
    ns = Namespace(name="canon_ns", slug="canon_ns", description="3.1")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    db.add(DataSource(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db_q",
        host="localhost",
        port=27017,
        username="",
        password="",
    ))
    await db.commit()
    return ns.id


@pytest_asyncio.fixture
async def seeded_db_q_canonical_collections(db, seeded_ns_with_canonicals) -> int:
    """3 条 SchemaCanonicalObject (mongodb, db_q.{c_category,c_product,c_quiz})."""
    ns_id = seeded_ns_with_canonicals
    for coll in ["c_category", "c_product", "c_quiz"]:
        db.add(SchemaCanonicalObject(
            namespace_id=ns_id,
            db_type="mongodb",
            database="db_q",
            target=coll,
            description="seed",
            purpose_detail="seed",
            reviewed=False,
        ))
    await db.commit()
    return ns_id


# ════════════════════════════════════════════
#  Tests
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_databases_returns_ds_list(admin_client, seeded_ns_with_3_ds):
    ns_id = seeded_ns_with_3_ds
    resp = await admin_client.get(f"/api/namespaces/{ns_id}/databases")
    assert resp.status_code == 200
    body = resp.json()
    assert "databases" in body
    items = body["databases"]
    assert len(items) == 3
    by_db = {item["database"]: item for item in items}
    assert by_db["db_q"]["db_type"] == "mongodb"
    assert by_db["db_main"]["db_type"] == "mysql"
    assert by_db["db_log"]["db_type"] == "mongodb"
    for item in items:
        assert "datasource_id" in item
        assert "host" in item
        assert isinstance(item["datasource_id"], int)


@pytest.mark.asyncio
async def test_get_collections_for_mongo_db(admin_client, seeded_db_q_canonical_collections):
    ns_id = seeded_db_q_canonical_collections
    resp = await admin_client.get(f"/api/namespaces/{ns_id}/collections?database=db_q")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "db_q"
    assert body["db_type"] == "mongodb"
    assert sorted(body["collections"]) == ["c_category", "c_product", "c_quiz"]


@pytest.mark.asyncio
async def test_get_collections_for_unknown_db_returns_empty(
    admin_client, seeded_db_q_canonical_collections,
):
    ns_id = seeded_db_q_canonical_collections
    resp = await admin_client.get(f"/api/namespaces/{ns_id}/collections?database=db_unknown")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "db_unknown"
    assert body["db_type"] is None
    assert body["collections"] == []


@pytest.mark.asyncio
async def test_get_collections_requires_database_param(
    admin_client, seeded_db_q_canonical_collections,
):
    ns_id = seeded_db_q_canonical_collections
    resp = await admin_client.get(f"/api/namespaces/{ns_id}/collections")
    assert resp.status_code == 422
