"""audit 端点作用域化: D (entry 反查) / Q (queue 收窄)。

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


@pytest.mark.asyncio
async def test_approve_foreign_entry_403(make_client, db):
    a = User(username="au_a", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_au_a")
    foreign = Namespace(name="au-f", slug="au-f-33", created_by=fo.id)
    db.add(foreign); await db.flush()
    entry = KnowledgeEntry(namespace_id=foreign.id, entry_type="rule", content="x", status="proposed")
    db.add(entry); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="au_a")
    resp = await client.post(f"/api/knowledge/audit/{entry.id}/approve", json={})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_queue_admin_scoped(make_client, db):
    """admin 的 queue 只返回自己 ns 的待审条目。"""
    a = User(username="au_b", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_au_b")
    mine = Namespace(name="au-mine", slug="au-mine-33", created_by=a.id)
    foreign = Namespace(name="au-foreign", slug="au-foreign-33", created_by=fo.id)
    db.add_all([mine, foreign]); await db.flush()
    db.add_all([
        KnowledgeEntry(namespace_id=mine.id, entry_type="rule", content="mine",
                       status="proposed", created_at=datetime.now()),
        KnowledgeEntry(namespace_id=foreign.id, entry_type="rule", content="foreign",
                       status="proposed", created_at=datetime.now()),
    ])
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="au_b")
    resp = await client.get("/api/knowledge/audit/queue")
    contents = {item["content"] for item in resp.json().get("items", [])}
    assert "mine" in contents
    assert "foreign" not in contents


@pytest.mark.asyncio
async def test_audit_queue_explicit_foreign_ns_403(make_client, db):
    """admin 显式请求他人 ns → 403。"""
    a = User(username="au_c", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_au_c")
    foreign = Namespace(name="au-f3", slug="au-f3-33", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="au_c")
    resp = await client.get("/api/knowledge/audit/queue", params={"namespace_id": foreign.id})
    assert resp.status_code == 403
