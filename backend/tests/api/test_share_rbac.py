"""share 作用域化: user 可分享/管理自己 ns 的历史, 越权 ns → 403。

注: QueryHistory 真实字段: namespace_id / session_id / role (user|assistant|system) /
content (用户原文) / result_snapshot。无 question 字段。
namespaces.created_by 有 FK, "他人拥有"的 ns 必须由真实 foreign 用户创建。
"""
import pytest
from app.models.user import User, UserNamespaceAccess
from app.models.namespace import Namespace
from app.models.query_history import QueryHistory


async def _foreign_owner(db, uname):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


@pytest.mark.asyncio
async def test_user_can_share_own_ns_history(make_client, db):
    u = User(username="sh_user", role="user", password_hash="x")
    db.add(u); await db.flush()
    fo = await _foreign_owner(db, "fo_sh_a")
    ns = Namespace(name="sh-ns", slug="sh-ns-38", created_by=fo.id)
    db.add(ns); await db.flush()
    db.add(UserNamespaceAccess(user_id=u.id, namespace_id=ns.id))
    h = QueryHistory(
        namespace_id=ns.id, session_id="s1", role="user", content="list orders",
        result_snapshot='{"rows":[]}',
    )
    db.add(h); await db.commit()
    client = await make_client(role="user", user_id=u.id, username="sh_user")
    resp = await client.post("/api/share", json={"query_history_id": h.id})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_user_cannot_share_foreign_ns_history(make_client, db):
    u = User(username="sh_user2", role="user", password_hash="x")
    db.add(u); await db.flush()
    fo = await _foreign_owner(db, "fo_sh_b")
    foreign = Namespace(name="sh-f", slug="sh-f-38", created_by=fo.id)
    db.add(foreign); await db.flush()
    h = QueryHistory(
        namespace_id=foreign.id, session_id="s2", role="user", content="list orders",
        result_snapshot='{"rows":[]}',
    )
    db.add(h); await db.commit()
    client = await make_client(role="user", user_id=u.id, username="sh_user2")
    resp = await client.post("/api/share", json={"query_history_id": h.id})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_share_list_user_sees_only_own(make_client, db):
    """GET /api/share — user 只看到自己有权 ns 的分享 (share→history→ns 反查过滤)。"""
    from app.models import SharedResult

    u = User(username="sh_list_user", role="user", password_hash="x")
    db.add(u); await db.flush()
    fo = await _foreign_owner(db, "fo_sh_list")
    own = Namespace(name="sh-own", slug="sh-own-38b", created_by=fo.id)
    other = Namespace(name="sh-other", slug="sh-other-38b", created_by=fo.id)
    db.add_all([own, other]); await db.flush()
    db.add(UserNamespaceAccess(user_id=u.id, namespace_id=own.id))  # 仅授权 own
    h_own = QueryHistory(
        namespace_id=own.id, session_id="so1", role="user", content="own q",
        result_snapshot='{"rows":[]}',
    )
    h_other = QueryHistory(
        namespace_id=other.id, session_id="so2", role="user", content="other q",
        result_snapshot='{"rows":[]}',
    )
    db.add_all([h_own, h_other]); await db.flush()
    db.add_all([
        SharedResult(token="tok-own-38b", query_history_id=h_own.id, shared_by=fo.id, is_active=True),
        SharedResult(token="tok-other-38b", query_history_id=h_other.id, shared_by=fo.id, is_active=True),
    ])
    await db.commit()

    client = await make_client(role="user", user_id=u.id, username="sh_list_user")
    resp = await client.get("/api/share")
    assert resp.status_code == 200
    tokens = {s["token"] for s in resp.json()}
    assert "tok-own-38b" in tokens
    assert "tok-other-38b" not in tokens
