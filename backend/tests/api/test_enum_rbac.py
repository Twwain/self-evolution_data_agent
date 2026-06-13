"""enum_dictionary 作用域化: Q (list 必填 query ns) / B (create) / D (put/delete/refs)。

注: namespaces.created_by 有 FK, "他人拥有"的 ns 必须由真实 foreign 用户创建。
"""
import pytest
from app.models.user import User
from app.models.namespace import Namespace
from app.models.enum_dictionary import EnumDictionary


async def _foreign_owner(db, uname):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


@pytest.mark.asyncio
async def test_enum_list_foreign_ns_403(make_client, db):
    a = User(username="en_a", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_en_a")
    foreign = Namespace(name="en-f", slug="en-f-35", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="en_a")
    resp = await client.get("/api/enum-dictionary", params={"namespace_id": foreign.id})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_enum_update_foreign_403(make_client, db):
    a = User(username="en_b", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_en_b")
    foreign = Namespace(name="en-f2", slug="en-f2-35", created_by=fo.id)
    db.add(foreign); await db.flush()
    e = EnumDictionary(namespace_id=foreign.id, enum_class_name="OrderStatus", values_json="[]")
    db.add(e); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="en_b")
    resp = await client.put(f"/api/enum-dictionary/{e.id}", json={"values": []})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_enum_list_own_ns_ok(make_client, db):
    a = User(username="en_c", role="admin", password_hash="x")
    db.add(a); await db.flush()
    ns = Namespace(name="en-own", slug="en-own-35", created_by=a.id)
    db.add(ns); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="en_c")
    resp = await client.get("/api/enum-dictionary", params={"namespace_id": ns.id})
    assert resp.status_code == 200
