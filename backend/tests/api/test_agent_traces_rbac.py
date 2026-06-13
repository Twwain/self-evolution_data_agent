"""agent_traces 作用域化: Q (list 收窄) + D (detail/refine 越权)。

注: namespaces.created_by 有 FK, "他人拥有"的 ns 必须由真实 foreign 用户创建。
"""
import pytest
from datetime import datetime
from app.models.user import User
from app.models.namespace import Namespace
from app.models.agent_trace import AgentTrace


async def _foreign_owner(db, uname):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


@pytest.mark.asyncio
async def test_traces_list_admin_scoped(make_client, db):
    a = User(username="tr_a", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_tr_a")
    mine = Namespace(name="tr-mine", slug="tr-mine-37", created_by=a.id)
    foreign = Namespace(name="tr-foreign", slug="tr-foreign-37", created_by=fo.id)
    db.add_all([mine, foreign]); await db.flush()
    db.add_all([
        AgentTrace(trace_id="t-mine-37", namespace_id=mine.id, user_query="q1",
                   created_at=datetime.now()),
        AgentTrace(trace_id="t-foreign-37", namespace_id=foreign.id, user_query="q2",
                   created_at=datetime.now()),
    ])
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="tr_a")
    resp = await client.get("/api/agent-traces")
    tids = {r["trace_id"] for r in resp.json()}
    assert "t-mine-37" in tids
    assert "t-foreign-37" not in tids


@pytest.mark.asyncio
async def test_traces_list_admin_explicit_foreign_ns_403(make_client, db):
    a = User(username="tr_b", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_tr_b")
    foreign = Namespace(name="tr-f2", slug="tr-f2-37", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="tr_b")
    resp = await client.get("/api/agent-traces", params={"namespace_id": foreign.id})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_trace_detail_foreign_ns_403(make_client, db):
    a = User(username="tr_c", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_tr_c")
    foreign = Namespace(name="tr-f3", slug="tr-f3-37", created_by=fo.id)
    db.add(foreign); await db.flush()
    db.add(AgentTrace(trace_id="t-detail-37", namespace_id=foreign.id, user_query="q",
                      created_at=datetime.now()))
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="tr_c")
    resp = await client.get("/api/agent-traces/t-detail-37")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_trace_detail_none_ns_only_super(make_client, db):
    """trace.namespace_id 为 None → 仅 super_admin 可见, 普通 admin 403。"""
    a = User(username="tr_d", role="admin", password_hash="x")
    db.add(a); await db.flush()
    db.add(AgentTrace(trace_id="t-none-37", namespace_id=None, user_query="q",
                      created_at=datetime.now()))
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="tr_d")
    resp = await client.get("/api/agent-traces/t-none-37")
    assert resp.status_code == 403
    super_client = await make_client(role="super_admin", user_id=a.id + 5000, username="root")
    resp2 = await super_client.get("/api/agent-traces/t-none-37")
    assert resp2.status_code == 200
