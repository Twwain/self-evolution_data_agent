"""Task 1: enum_sync_queue + enum_binding_conflicts 模型 + 唯一约束测试."""
import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.models.enum_binding_conflict import EnumBindingConflict
from app.models.enum_sync_queue import EnumSyncQueue
from app.models.namespace import Namespace


@pytest_asyncio.fixture
async def ns(async_session):
    async with async_session() as db:
        n = Namespace(name="sync_test", slug="sync_test", description="")
        db.add(n)
        await db.commit()
        await db.refresh(n)
        return n


@pytest.mark.asyncio
async def test_queue_insert(async_session, ns):
    async with async_session() as db:
        row = EnumSyncQueue(enum_dict_id=42, namespace_id=ns.id, event="create")
        db.add(row)
        await db.commit()
        assert row.id is not None


@pytest.mark.asyncio
async def test_conflict_unique_open(async_session, ns):
    """同 (field, enum) 同时只能有一条 status='open'."""
    async with async_session() as db:
        c1 = EnumBindingConflict(
            namespace_id=ns.id, field_canonical_id=10, field_name="status",
            enum_dict_id=42, conflict_kind="value_not_covered",
            detail_json="{}", status="open",
        )
        db.add(c1)
        await db.commit()

        # 再插一条同 (field, enum) status='open' → 拒绝
        c2 = EnumBindingConflict(
            namespace_id=ns.id, field_canonical_id=10, field_name="status",
            enum_dict_id=42, conflict_kind="value_not_covered",
            detail_json="{}", status="open",
        )
        db.add(c2)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()

    # resolved 行可同时存在 → 新 open 不冲突
    async with async_session() as db:
        # 把第一条改为 resolved
        from sqlalchemy import select
        c1_loaded = (await db.execute(
            select(EnumBindingConflict).where(EnumBindingConflict.field_name == "status")
        )).scalar_one()
        c1_loaded.status = "resolved"
        await db.commit()

        # 再插新 open → 成功
        c3 = EnumBindingConflict(
            namespace_id=ns.id, field_canonical_id=10, field_name="status",
            enum_dict_id=42, conflict_kind="value_not_covered",
            detail_json="{}", status="open",
        )
        db.add(c3)
        await db.commit()  # 不报错
        assert c3.id is not None
