"""schema_canonical (v1+v2) P 类作用域化: 越权 admin 403 / user 即便有 ns 权也 403。

注: namespaces.created_by 有 FK, "他人拥有"的 ns 必须由真实 foreign 用户创建。
"""
import pytest
from app.models.user import User, UserNamespaceAccess
from app.models.namespace import Namespace


async def _foreign_owner(db, uname):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


@pytest.mark.asyncio
async def test_schema_list_foreign_ns_403(make_client, db):
    a = User(username="sc_a", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_sc_a")
    foreign = Namespace(name="sc-f", slug="sc-f-34", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="sc_a")
    resp = await client.get(f"/api/namespaces/{foreign.id}/schema-canonical")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_schema_v2_promote_foreign_ns_403(make_client, db):
    a = User(username="sc_b", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_sc_b")
    foreign = Namespace(name="sc-f2", slug="sc-f2-34", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="sc_b")
    resp = await client.post(f"/api/namespaces/{foreign.id}/schema-canonical/promote")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_user_cannot_access_schema_pages(make_client, db):
    """user 无高级页准入 (require_ns_manage 要求 admin+)。"""
    u = User(username="sc_user", role="user", password_hash="x")
    db.add(u); await db.flush()
    fo = await _foreign_owner(db, "fo_sc_user")
    ns = Namespace(name="sc-ns", slug="sc-ns-34", created_by=fo.id)
    db.add(ns); await db.flush()
    db.add(UserNamespaceAccess(user_id=u.id, namespace_id=ns.id))  # 即便授权
    await db.commit()
    client = await make_client(role="user", user_id=u.id, username="sc_user")
    resp = await client.get(f"/api/namespaces/{ns.id}/schema-canonical")
    assert resp.status_code == 403  # user 即便有 ns 权也无高级页准入


@pytest.mark.asyncio
async def test_schema_list_own_ns_ok(make_client, db):
    """有权 admin 通: owner 可访问。"""
    a = User(username="sc_owner", role="admin", password_hash="x")
    db.add(a); await db.flush()
    ns = Namespace(name="sc-own", slug="sc-own-34", created_by=a.id)
    db.add(ns); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="sc_owner")
    resp = await client.get(f"/api/namespaces/{ns.id}/schema-canonical")
    assert resp.status_code == 200
