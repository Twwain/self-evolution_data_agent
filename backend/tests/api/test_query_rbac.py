"""query/stream 安全缺口: 无 ns 权的用户不能查询任意 namespace。

注: namespaces.created_by 有 FK 约束, "他人拥有"的 ns 必须由真实存在的
foreign 用户创建 (不能用不存在的 id 9999 占位)。
"""
import pytest
from app.models.user import User
from app.models.namespace import Namespace


async def _foreign_owner(db, uname):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


@pytest.mark.asyncio
async def test_user_cannot_query_unauthorized_namespace(make_client, db):
    u = User(username="q_user", role="user", password_hash="x")
    db.add(u); await db.flush()
    fo = await _foreign_owner(db, "fo_q31")
    foreign = Namespace(name="Foreign Q", slug="foreign-q31", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="user", user_id=u.id, username="q_user")
    resp = await client.post("/api/query/stream", json={
        "namespace_id": foreign.id, "question": "count orders",
    })
    assert resp.status_code == 403  # 修复前: 放行 (200/SSE)
