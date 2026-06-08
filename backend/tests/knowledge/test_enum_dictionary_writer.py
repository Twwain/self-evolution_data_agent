"""upsert_enum_dictionary_from_code 单元测试.

覆盖:
- 新建 code 行
- 更新已有 code 行
- 跳过 manual 行 (不覆盖)
"""
import json

import pytest
from sqlalchemy import select

from app.knowledge.enum_dictionary_writer import upsert_enum_dictionary_from_code
from app.knowledge.enum_extractor import EnumDef, EnumValue
from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace
from app.models.user import User


@pytest.fixture
def order_enum():
    return EnumDef(
        enum_class="OrderStatus",
        fully_qualified_name="com.x.OrderStatus",
        values=[
            EnumValue(name="CREATED", db_value=1, description="已创建"),
            EnumValue(name="PAID", db_value=2, description="已支付"),
        ],
    )


@pytest.mark.asyncio
async def test_upsert_creates_new_code_row(async_session, order_enum):
    async with async_session() as db:
        ns = Namespace(name="writer_test", slug="writer_test", description="t")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        eid = await upsert_enum_dictionary_from_code(db, ns.id, order_enum)
        await db.commit()

        row = (
            await db.execute(
                select(EnumDictionary).where(EnumDictionary.id == eid)
            )
        ).scalar_one()
        assert row.source == "code"
        assert row.enum_class_name == "OrderStatus"
        assert len(json.loads(row.values_json)) == 2


@pytest.mark.asyncio
async def test_upsert_updates_existing_code_row(async_session, order_enum):
    async with async_session() as db:
        ns = Namespace(name="writer_upd", slug="writer_upd", description="t")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        eid1 = await upsert_enum_dictionary_from_code(db, ns.id, order_enum)
        await db.commit()

        # 第二次, values 多了一个
        extended = EnumDef(
            enum_class="OrderStatus",
            fully_qualified_name="com.x.OrderStatus",
            values=[
                EnumValue(name="CREATED", db_value=1, description="已创建"),
                EnumValue(name="PAID", db_value=2, description="已支付"),
                EnumValue(name="SHIPPED", db_value=3, description="已发货"),
            ],
        )
        eid2 = await upsert_enum_dictionary_from_code(db, ns.id, extended)
        await db.commit()

        assert eid1 == eid2
        row = (
            await db.execute(
                select(EnumDictionary).where(EnumDictionary.id == eid1)
            )
        ).scalar_one()
        assert len(json.loads(row.values_json)) == 3


@pytest.mark.asyncio
async def test_upsert_skips_manual_row(async_session, order_enum):
    async with async_session() as db:
        ns = Namespace(name="writer_manual", slug="writer_manual", description="t")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        user = User(username="manual_user", password_hash="x", role="admin")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        manual = EnumDictionary(
            namespace_id=ns.id,
            enum_class_name="OrderStatus",
            values_json='[{"name":"X","db_value":99,"description":"manual"}]',
            source="manual",
            created_by=user.id,
        )
        db.add(manual)
        await db.commit()
        await db.refresh(manual)

        eid = await upsert_enum_dictionary_from_code(db, ns.id, order_enum)
        await db.commit()

        assert eid == manual.id
        await db.refresh(manual)
        # manual values 未被覆盖
        vals = json.loads(manual.values_json)
        assert vals[0]["db_value"] == 99
        assert manual.source == "manual"
