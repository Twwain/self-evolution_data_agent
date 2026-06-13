"""knowledge 端点作用域化: P (path ns) / D (entry 反查) / B (body ns)。

注: namespaces.created_by 有 FK, "他人拥有"的 ns 必须由真实 foreign 用户创建。
"""
import pytest
from datetime import datetime
from app.models.user import User
from app.models.namespace import Namespace
from app.models.knowledge_entry import KnowledgeEntry


async def _foreign_owner(db, uname):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


async def _admin_with_ns(db, uname, slug):
    a = User(username=uname, role="admin", password_hash="x")
    db.add(a); await db.flush()
    ns = Namespace(name=slug, slug=slug, created_by=a.id)
    db.add(ns); await db.flush()
    return a, ns


@pytest.mark.asyncio
async def test_P_list_knowledge_foreign_ns_403(make_client, db):
    a, _ = await _admin_with_ns(db, "kn_a", "kn-a-32")
    fo = await _foreign_owner(db, "fo_kn_a")
    foreign = Namespace(name="kn-foreign", slug="kn-foreign-32", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="kn_a")
    resp = await client.get(f"/api/namespaces/{foreign.id}/knowledge")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_D_get_entry_foreign_ns_403(make_client, db):
    a, _ = await _admin_with_ns(db, "kn_b", "kn-b-32")
    fo = await _foreign_owner(db, "fo_kn_b")
    foreign = Namespace(name="kn-f2", slug="kn-f2-32", created_by=fo.id)
    db.add(foreign); await db.flush()
    entry = KnowledgeEntry(namespace_id=foreign.id, entry_type="rule", content="x")
    db.add(entry); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="kn_b")
    resp = await client.get(f"/api/knowledge/{entry.id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_B_create_knowledge_foreign_ns_403(make_client, db):
    a, _ = await _admin_with_ns(db, "kn_c", "kn-c-32")
    fo = await _foreign_owner(db, "fo_kn_c")
    foreign = Namespace(name="kn-f3", slug="kn-f3-32", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="kn_c")
    resp = await client.post("/api/knowledge", json={
        "namespace_id": foreign.id, "entry_type": "rule", "content": "x",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_can_access_foreign_entry(make_client, db):
    """super_admin 全通: 越权检查对 super_admin 豁免。"""
    fo = await _foreign_owner(db, "fo_kn_super")
    foreign = Namespace(name="kn-sup", slug="kn-sup-32", created_by=fo.id)
    db.add(foreign); await db.flush()
    entry = KnowledgeEntry(namespace_id=foreign.id, entry_type="rule", content="x",
                           created_at=datetime.now())
    db.add(entry); await db.commit()
    client = await make_client(role="super_admin", user_id=fo.id + 1000, username="root")
    resp = await client.get(f"/api/knowledge/{entry.id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_owner_admin_can_access_own_entry(make_client, db):
    """有权 admin 通: owner 的 entry 可访问。"""
    a, ns = await _admin_with_ns(db, "kn_owner", "kn-owner-32")
    entry = KnowledgeEntry(namespace_id=ns.id, entry_type="rule", content="x",
                           created_at=datetime.now())
    db.add(entry); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="kn_owner")
    resp = await client.get(f"/api/knowledge/{entry.id}")
    assert resp.status_code == 200
