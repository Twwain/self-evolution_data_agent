"""RBAC 模型列变更: role 列宽 + namespace.created_by。"""
import pytest
from sqlalchemy import select
from app.models.user import User
from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_role_column_holds_super_admin(db):
    u = User(username="super1", role="super_admin", password_hash="x")
    db.add(u)
    await db.commit()
    row = (await db.execute(select(User).where(User.username == "super1"))).scalar_one()
    assert row.role == "super_admin"  # 11 字符, String(10) 会截断/报错


@pytest.mark.asyncio
async def test_namespace_created_by(db):
    owner = User(username="owner1", role="admin", password_hash="x")
    db.add(owner)
    await db.flush()
    ns = Namespace(name="P1 NS", slug="p1-ns", description="", created_by=owner.id)
    db.add(ns)
    await db.commit()
    row = (await db.execute(select(Namespace).where(Namespace.slug == "p1-ns"))).scalar_one()
    assert row.created_by == owner.id


@pytest.mark.asyncio
async def test_namespace_created_by_nullable(db):
    ns = Namespace(name="Orphan NS", slug="orphan-ns", description="")
    db.add(ns)
    await db.commit()
    row = (await db.execute(select(Namespace).where(Namespace.slug == "orphan-ns"))).scalar_one()
    assert row.created_by is None


@pytest.mark.asyncio
async def test_delete_admin_nulls_child_created_by(db):
    """删除有下属的 admin → 子用户 created_by 置 NULL (ON DELETE SET NULL), 不 IntegrityError。

    注: 测试库表由 Base.metadata.create_all 建, 模型补 ondelete 后新表 FK 即带 SET NULL。
    存量库的旧 FK 由 migration_021 第 5 步修复 (见 Task 1.6)。
    """
    parent = User(username="parent_admin", role="admin", password_hash="x")
    db.add(parent); await db.flush()
    child = User(username="child_user", role="user", password_hash="x", created_by=parent.id)
    db.add(child); await db.flush()
    child_id = child.id
    await db.delete(parent)
    await db.flush()
    db.expire_all()  # 清 identity map, 强制重读 DB (SET NULL 由 PG 在 DELETE 时应用)
    row = (await db.execute(select(User).where(User.id == child_id))).scalar_one()
    assert row.created_by is None  # 自动置 NULL, 未抛 IntegrityError


def test_namespace_out_has_created_by():
    from app.schemas import NamespaceOut
    assert "created_by" in NamespaceOut.model_fields
