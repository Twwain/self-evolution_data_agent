"""EnumDictionary ORM 模型回归.

覆盖:
- 默认 source='code', scope='namespace', comment=''
- (namespace_id, enum_class_name) 唯一约束
- source='manual' + created_by 持久化
"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace
from app.models.user import User


@pytest.mark.asyncio
async def test_default_source_code(async_session):
    async with async_session() as db:
        ns = Namespace(name="enum_test", slug="enum_test", description="test")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        e = EnumDictionary(
            namespace_id=ns.id,
            enum_class_name="OrderStatus",
            values_json='[{"name":"A","db_value":1}]',
        )
        db.add(e)
        await db.commit()
        await db.refresh(e)
        assert e.source == "code"
        assert e.scope == "namespace"
        assert e.comment == ""


@pytest.mark.asyncio
async def test_unique_ns_class_name(async_session):
    async with async_session() as db:
        ns = Namespace(name="enum_uq", slug="enum_uq", description="test")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        e1 = EnumDictionary(
            namespace_id=ns.id,
            enum_class_name="DupEnum",
            values_json='[{"name":"A","db_value":1}]',
        )
        db.add(e1)
        await db.commit()

        e2 = EnumDictionary(
            namespace_id=ns.id,
            enum_class_name="DupEnum",
            values_json='[{"name":"B","db_value":2}]',
        )
        db.add(e2)
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_manual_source_persisted(async_session):
    async with async_session() as db:
        ns = Namespace(name="enum_manual", slug="enum_manual", description="test")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        user = User(username="tester", password_hash="x", role="admin")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        e = EnumDictionary(
            namespace_id=ns.id,
            enum_class_name="DeleteStatus",
            values_json='[{"name":"NORMAL","db_value":0}]',
            source="manual",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(e)
        await db.commit()
        await db.refresh(e)
        assert e.source == "manual"
        assert e.created_by == user.id
