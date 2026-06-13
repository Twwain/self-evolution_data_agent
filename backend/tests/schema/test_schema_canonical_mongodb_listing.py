"""T7 — schema-canonical mongodb listing API 测试 (从原 test_mongo_canonical_api.py 改写).

设计源: spec 04-implementation-plan.md T7. 验证
GET /api/namespaces/{ns_id}/schema-canonical?db_type=mongodb 端点是
mongo collection 列表的唯一访问入口 (取代旧 /mongo-canonical 路由).

通用业务集合示例 (orders / products / users), 通过 CLAUDE.md 6 条产品化 checklist.
fixtures: tests/schema/conftest.py 的 test_session + namespace_factory + http_client (httpx).
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.metadata import get_db
from app.main import app
from app.models import Namespace, SchemaCanonicalObject
from app.models.user import User

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def admin_user(test_session: AsyncSession) -> User:
    user = User(username="admin_mongo_listing", password_hash="x", role="super_admin", is_active=True)
    test_session.add(user)
    await test_session.flush()
    return user


@pytest_asyncio.fixture
async def http_client(
    test_session: AsyncSession, admin_user: User,
) -> AsyncGenerator[AsyncClient, None]:
    async def _override_db():
        yield test_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


def _make_sco(ns_id: int, db_type: str, target: str) -> SchemaCanonicalObject:
    """构造最小 SchemaCanonicalObject."""
    return SchemaCanonicalObject(
        namespace_id=ns_id,
        db_type=db_type,
        database="db1",
        target=target,
        fields_json=json.dumps([{"name": "id", "type": "ObjectId"}]),
        indexes_json="[]",
        description=f"{target} 集合" if db_type == "mongodb" else f"{target} 表",
        purpose_detail="",
        sample_count=100,
        source="introspect",
        relationships_json="[]",
        sample_values_json="[]",
        user_locked=False,
    )


@pytest_asyncio.fixture
async def ns_with_mixed_canonicals(test_session: AsyncSession, namespace_factory):
    """ns 内含 2 个 mongodb collection + 1 个 mysql 表, 验证 db_type filter."""
    ns = await namespace_factory()
    test_session.add(_make_sco(ns.id, "mongodb", "orders"))
    test_session.add(_make_sco(ns.id, "mongodb", "products"))
    test_session.add(_make_sco(ns.id, "mysql", "users"))
    await test_session.flush()
    return ns


class TestSchemaCanonicalMongodbListing:
    """GET /api/namespaces/{ns_id}/schema-canonical?db_type=mongodb 替代旧 mongo-canonical 端点."""

    async def test_returns_only_mongodb_when_filtered(
        self, http_client: AsyncClient, ns_with_mixed_canonicals: Namespace,
    ):
        """db_type=mongodb 过滤 → 仅返 2 条 mongodb, 不含 mysql."""
        ns = ns_with_mixed_canonicals
        resp = await http_client.get(
            f"/api/namespaces/{ns.id}/schema-canonical",
            params={"db_type": "mongodb"},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        assert {r["target"] for r in rows} == {"orders", "products"}
        assert all(r["db_type"] == "mongodb" for r in rows)

    async def test_returns_all_when_no_filter(
        self, http_client: AsyncClient, ns_with_mixed_canonicals: Namespace,
    ):
        """无 db_type 参数 → 返 mysql + mongodb 全部 3 条."""
        ns = ns_with_mixed_canonicals
        resp = await http_client.get(f"/api/namespaces/{ns.id}/schema-canonical")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 3

    async def test_mysql_filter_excludes_mongodb(
        self, http_client: AsyncClient, ns_with_mixed_canonicals: Namespace,
    ):
        """对偶验证: db_type=mysql → 仅返 1 条 mysql."""
        ns = ns_with_mixed_canonicals
        resp = await http_client.get(
            f"/api/namespaces/{ns.id}/schema-canonical",
            params={"db_type": "mysql"},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["target"] == "users"
        assert rows[0]["db_type"] == "mysql"
