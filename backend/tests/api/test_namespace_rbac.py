"""namespace 归属: 创建写 created_by + admin 自动授权 + 删改 owner-only + list 过滤
+ 子资源端点作用域化 (Phase 3.9)。

注: namespaces.created_by / users.created_by 有 FK 约束 (migration_021), 故"他人拥有"
的 ns 必须由一个真实存在的 foreign 用户创建, 不能用不存在的 id 9999 占位。
"""
import pytest
from sqlalchemy import select
from app.models.user import User, UserNamespaceAccess
from app.models.namespace import Namespace


async def _foreign_owner(db, uname="ns_foreign_owner"):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


@pytest.mark.asyncio
async def test_admin_create_sets_owner_and_grants_self(make_client, db):
    a = User(username="ns_creator", role="admin", password_hash="x")
    db.add(a); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="ns_creator")
    resp = await client.post("/api/namespaces", json={
        "name": "Sales Region", "slug": "sales-region", "description": "",
    })
    assert resp.status_code == 201
    ns = (await db.execute(select(Namespace).where(Namespace.slug == "sales-region"))).scalar_one()
    assert ns.created_by == a.id
    acc = (await db.execute(select(UserNamespaceAccess).where(
        UserNamespaceAccess.user_id == a.id, UserNamespaceAccess.namespace_id == ns.id
    ))).scalar_one_or_none()
    assert acc is not None


@pytest.mark.asyncio
async def test_admin_cannot_delete_unowned_namespace(make_client, db):
    a = User(username="ns_admin_a", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_del")
    foreign = Namespace(name="Foreign", slug="foreign-26", created_by=fo.id)
    db.add(foreign); await db.flush()
    # 即便被授予访问权, 非 owner 也不能删本体
    db.add(UserNamespaceAccess(user_id=a.id, namespace_id=foreign.id))
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="ns_admin_a")
    resp = await client.request("DELETE", f"/api/namespaces/{foreign.id}", params={"dry_run": "false"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_filters_namespaces(make_client, db):
    a = User(username="ns_lister", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_list")
    mine = Namespace(name="Mine", slug="mine-26", created_by=a.id)
    theirs = Namespace(name="Theirs", slug="theirs-26", created_by=fo.id)
    db.add_all([mine, theirs]); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="ns_lister")
    resp = await client.get("/api/namespaces")
    slugs = {n["slug"] for n in resp.json()}
    assert "mine-26" in slugs
    assert "theirs-26" not in slugs


@pytest.mark.asyncio
async def test_datasource_list_foreign_ns_403(make_client, db):
    """P 类子资源: admin 对无权 ns 的 datasources 端点 → 403。"""
    a = User(username="ds_admin", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_ds")
    foreign = Namespace(name="DS Foreign", slug="ds-foreign-39", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="ds_admin")
    resp = await client.get(f"/api/namespaces/{foreign.id}/datasources")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_databases_foreign_ns_403(make_client, db):
    a = User(username="db_admin", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_db")
    foreign = Namespace(name="DB Foreign", slug="db-foreign-39", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="db_admin")
    resp = await client.get(f"/api/namespaces/{foreign.id}/databases")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_user_cannot_access_datasource_endpoints(make_client, db):
    """user 无高级管理页准入, 即便对授权 ns 的子资源也 403 (require_ns_manage = admin+)。"""
    u = User(username="ds_user", role="user", password_hash="x")
    db.add(u); await db.flush()
    fo = await _foreign_owner(db, "fo_dsuser")
    ns = Namespace(name="DS NS", slug="ds-ns-39", created_by=fo.id)
    db.add(ns); await db.flush()
    db.add(UserNamespaceAccess(user_id=u.id, namespace_id=ns.id))
    await db.commit()
    client = await make_client(role="user", user_id=u.id, username="ds_user")
    resp = await client.get(f"/api/namespaces/{ns.id}/datasources")
    assert resp.status_code == 403  # user 即便有 ns 权也无高级管理页准入
