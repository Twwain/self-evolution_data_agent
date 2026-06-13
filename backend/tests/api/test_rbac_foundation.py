"""三级 RBAC 授权地基单元测试 — 角色层级 + 作用域断言 + 可访问集合。"""
import pytest
from fastapi import HTTPException
from app.auth import (
    ROLE_USER, ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_LEVEL, role_at_least,
    assert_ns_access, assert_ns_owner, accessible_namespace_ids,
)
from app.models.user import User, UserNamespaceAccess
from app.models.namespace import Namespace


def _u(role: str) -> User:
    return User(id=1, username="u", role=role, password_hash="x")


def test_role_level_ordering():
    assert ROLE_LEVEL[ROLE_USER] < ROLE_LEVEL[ROLE_ADMIN] < ROLE_LEVEL[ROLE_SUPER_ADMIN]


def test_role_at_least():
    assert role_at_least(_u(ROLE_SUPER_ADMIN), ROLE_ADMIN) is True
    assert role_at_least(_u(ROLE_ADMIN), ROLE_ADMIN) is True
    assert role_at_least(_u(ROLE_USER), ROLE_ADMIN) is False
    assert role_at_least(_u(ROLE_USER), ROLE_USER) is True


def test_role_at_least_unknown_role_is_lowest():
    assert role_at_least(_u("garbage"), ROLE_USER) is False


@pytest.mark.asyncio
async def test_assert_ns_access_super_admin_bypass(db):
    su = User(username="su", role="super_admin", password_hash="x")
    db.add(su); await db.flush()
    # 不存在的 ns 也豁免 (super_admin 全局)
    await assert_ns_access(db, su, 999999)  # 不抛


@pytest.mark.asyncio
async def test_assert_ns_access_owner_and_granted(db):
    admin = User(username="a1", role="admin", password_hash="x")
    other = User(username="a2", role="admin", password_hash="x")
    db.add_all([admin, other]); await db.flush()
    owned = Namespace(name="Owned", slug="owned-115", created_by=admin.id)
    granted = Namespace(name="Granted", slug="granted-115", created_by=other.id)
    foreign = Namespace(name="Foreign", slug="foreign-115", created_by=other.id)
    db.add_all([owned, granted, foreign]); await db.flush()
    db.add(UserNamespaceAccess(user_id=admin.id, namespace_id=granted.id))
    await db.flush()
    await assert_ns_access(db, admin, owned.id)    # owner ok
    await assert_ns_access(db, admin, granted.id)  # granted ok
    with pytest.raises(HTTPException) as ei:
        await assert_ns_access(db, admin, foreign.id)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_assert_ns_owner_granted_not_enough(db):
    admin = User(username="a3", role="admin", password_hash="x")
    other = User(username="a4", role="admin", password_hash="x")
    db.add_all([admin, other]); await db.flush()
    granted = Namespace(name="G2", slug="g2-115", created_by=other.id)
    db.add(granted); await db.flush()
    db.add(UserNamespaceAccess(user_id=admin.id, namespace_id=granted.id))
    await db.flush()
    # granted 但非 owner → assert_ns_owner 拒绝
    with pytest.raises(HTTPException) as ei:
        await assert_ns_owner(db, admin, granted.id)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_accessible_namespace_ids(db):
    su = User(username="su2", role="super_admin", password_hash="x")
    admin = User(username="a5", role="admin", password_hash="x")
    db.add_all([su, admin]); await db.flush()
    ns = Namespace(name="N1", slug="n1-115", created_by=admin.id)
    db.add(ns); await db.flush()
    assert await accessible_namespace_ids(db, su) is None            # super → None
    ids = await accessible_namespace_ids(db, admin)
    assert ns.id in ids                                              # admin → owner included
