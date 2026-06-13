"""用户管辖边界: can_manage + 可建角色 + 越权拒绝 + 自锁防护 + 重置密码。"""
import pytest
from app.api.users import can_manage, CREATABLE_ROLES
from app.auth import ROLE_USER, ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.models.user import User


def _u(uid, role, created_by=None):
    return User(id=uid, username=f"u{uid}", role=role, password_hash="x", created_by=created_by)


def test_creatable_roles():
    assert CREATABLE_ROLES[ROLE_SUPER_ADMIN] == {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER}
    assert CREATABLE_ROLES[ROLE_ADMIN] == {ROLE_USER}


def test_super_admin_manages_anyone():
    su = _u(1, ROLE_SUPER_ADMIN)
    assert can_manage(su, _u(2, ROLE_ADMIN)) is True
    assert can_manage(su, _u(3, ROLE_USER)) is True
    assert can_manage(su, _u(4, ROLE_SUPER_ADMIN)) is True


def test_admin_manages_only_own_users():
    admin = _u(10, ROLE_ADMIN)
    own_user = _u(11, ROLE_USER, created_by=10)
    other_user = _u(12, ROLE_USER, created_by=99)
    another_admin = _u(13, ROLE_ADMIN, created_by=10)  # 即使 created_by 自己, admin 也管不了 admin
    assert can_manage(admin, own_user) is True
    assert can_manage(admin, other_user) is False
    assert can_manage(admin, another_admin) is False


def test_user_manages_nobody():
    assert can_manage(_u(20, ROLE_USER), _u(21, ROLE_USER)) is False


@pytest.mark.asyncio
async def test_assert_not_last_super_admin_blocks_last(db):
    """最后一个 active super_admin 触发 400; 有第二个时放行。"""
    from app.api.users import assert_not_last_super_admin
    from fastapi import HTTPException
    from sqlalchemy import update
    from sqlalchemy import update
    # 确保测试事务内无其他 super_admin (共享测试库可能有 admin→super_admin 残留)
    await db.execute(
        update(User).where(User.role == "super_admin").values(role="admin")
    )
    su1 = User(username="su_lock1", role="super_admin", password_hash="x")
    db.add(su1); await db.flush()
    with pytest.raises(HTTPException) as ei:
        await assert_not_last_super_admin(db, su1)
    assert ei.value.status_code == 400
    su2 = User(username="su_lock2", role="super_admin", password_hash="x")
    db.add(su2); await db.flush()
    await assert_not_last_super_admin(db, su1)  # 不抛
    normal = User(username="su_lock3", role="user", password_hash="x")
    db.add(normal); await db.flush()
    await assert_not_last_super_admin(db, normal)


@pytest.mark.asyncio
async def test_admin_cannot_create_admin(make_client, db):
    admin = User(username="creator_admin", role="admin", password_hash="x")
    db.add(admin); await db.commit()
    client = await make_client(role="admin", user_id=admin.id, username="creator_admin")
    resp = await client.post("/api/users", json={
        "username": "new_admin", "password": "order123", "role": "admin",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_creates_user_sets_created_by(make_client, db):
    from sqlalchemy import select
    admin = User(username="creator2", role="admin", password_hash="x")
    db.add(admin); await db.commit()
    client = await make_client(role="admin", user_id=admin.id, username="creator2")
    resp = await client.post("/api/users", json={
        "username": "child_user", "password": "order123", "role": "user",
    })
    assert resp.status_code == 201
    row = (await db.execute(select(User).where(User.username == "child_user"))).scalar_one()
    assert row.created_by == admin.id


@pytest.mark.asyncio
async def test_admin_list_sees_only_own_users(make_client, db):
    a = User(username="lister_admin", role="admin", password_hash="x")
    fo = User(username="lister_foreign", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    db.add_all([
        User(username="mychild", role="user", password_hash="x", created_by=a.id),
        User(username="otherchild", role="user", password_hash="x", created_by=fo.id),
    ])
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="lister_admin")
    resp = await client.get("/api/users")
    names = {u["username"] for u in resp.json()}
    assert "mychild" in names
    assert "otherchild" not in names


@pytest.mark.asyncio
async def test_admin_cannot_update_other_admins_user(make_client, db):
    a = User(username="upd_admin", role="admin", password_hash="x")
    fo = User(username="upd_foreign", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    victim = User(username="not_mine", role="user", password_hash="x", created_by=fo.id)
    db.add(victim); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="upd_admin")
    resp = await client.put(f"/api/users/{victim.id}", json={"is_active": False})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cannot_delete_last_super_admin(make_client, db):
    su = User(username="only_super", role="super_admin", password_hash="x")
    db.add(su); await db.commit()
    client = await make_client(role="super_admin", user_id=su.id, username="only_super")
    resp = await client.delete(f"/api/users/{su.id}")
    # 既是自己又是最后一个 super_admin → 400 (自删防护或自锁防护任一触发)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_cannot_grant_ns_outside_own_scope(make_client, db):
    from app.models.namespace import Namespace
    a = User(username="grant_admin", role="admin", password_hash="x")
    fo = User(username="grant_foreign", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    own = Namespace(name="Own NS", slug="own-24", created_by=a.id)
    foreign = Namespace(name="Foreign NS", slug="foreign-24", created_by=fo.id)
    db.add_all([own, foreign]); await db.flush()
    child = User(username="grant_child", role="user", password_hash="x", created_by=a.id)
    db.add(child); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="grant_admin")
    resp = await client.put(f"/api/users/{child.id}/access", json={
        "namespace_ids": [own.id, foreign.id],
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_resets_own_user_password(make_client, db):
    from app.auth import verify_password
    from sqlalchemy import select
    a = User(username="reset_admin", role="admin", password_hash="x")
    db.add(a); await db.flush()
    child = User(username="reset_child", role="user", password_hash="x", created_by=a.id)
    db.add(child); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="reset_admin")
    resp = await client.post(f"/api/users/{child.id}/reset-password", json={
        "new_password": "newpass123",
    })
    assert resp.status_code == 200
    row = (await db.execute(select(User).where(User.id == child.id))).scalar_one()
    assert verify_password("newpass123", row.password_hash)


@pytest.mark.asyncio
async def test_admin_cannot_reset_foreign_user(make_client, db):
    a = User(username="reset_admin2", role="admin", password_hash="x")
    fo = User(username="reset_foreign", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    victim = User(username="foreign_child", role="user", password_hash="x", created_by=fo.id)
    db.add(victim); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="reset_admin2")
    resp = await client.post(f"/api/users/{victim.id}/reset-password", json={
        "new_password": "newpass123",
    })
    assert resp.status_code == 403
