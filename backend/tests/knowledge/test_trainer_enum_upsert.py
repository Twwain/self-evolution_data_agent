"""trainer 解析 enum 后调 upsert_enum_dictionary_from_code 集成验证.

验证: code_result.enum_classes 中的 dict 被正确转换为 EnumDef 并 upsert 到
EnumDictionary 表.
"""
import json

import pytest
from sqlalchemy import select

from app.knowledge.enum_dictionary_writer import upsert_enum_dictionary_from_code
from app.knowledge.enum_extractor import EnumDef, EnumValue
from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_enum_dict_from_code_result_format(async_session):
    """模拟 trainer 中 code_result.enum_classes 的 dict 格式, 验证转换 + upsert."""
    async with async_session() as db:
        ns = Namespace(name="trainer_enum", slug="trainer_enum", description="t")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        # 模拟 code_result.enum_classes 的 dict 格式 (来自 _parse_enum_classes_batch)
        enum_classes_raw: list[dict] = [
            {
                "enum_class": "OrderStatus",
                "fully_qualified_name": "com.example.OrderStatus",
                "values": [
                    {"name": "CREATED", "db_value": 1, "description": "已创建"},
                    {"name": "PAID", "db_value": 2, "description": "已支付"},
                ],
            },
            {
                "enum_class": "DeleteFlag",
                "fully_qualified_name": "com.example.DeleteFlag",
                "values": [
                    {"name": "NORMAL", "db_value": 0, "description": "正常"},
                    {"name": "DELETED", "db_value": 1, "description": "已删除"},
                ],
            },
        ]

        # 复现 trainer 中的转换逻辑
        for ec in enum_classes_raw:
            values = ec.get("values") or []
            enum_def = EnumDef(
                enum_class=ec.get("enum_class", ""),
                fully_qualified_name=ec.get("fully_qualified_name", ""),
                values=[
                    EnumValue(
                        name=v["name"],
                        db_value=v.get("db_value", v["name"]),
                        description=v.get("description"),
                    )
                    for v in values
                ],
            )
            if enum_def.enum_class:
                await upsert_enum_dictionary_from_code(db, ns.id, enum_def)
        await db.commit()

        # 验证 2 行被创建
        rows = (
            await db.execute(
                select(EnumDictionary).where(
                    EnumDictionary.namespace_id == ns.id
                )
            )
        ).scalars().all()
        assert len(rows) == 2

        by_name = {r.enum_class_name: r for r in rows}
        assert "OrderStatus" in by_name
        assert "DeleteFlag" in by_name

        order_vals = json.loads(by_name["OrderStatus"].values_json)
        assert len(order_vals) == 2
        assert order_vals[0]["name"] == "CREATED"
        assert by_name["OrderStatus"].source == "code"
        assert by_name["OrderStatus"].fully_qualified_name == "com.example.OrderStatus"


@pytest.mark.asyncio
async def test_idempotent_rerun(async_session):
    """重复跑 trainer 不会重复创建 (幂等)."""
    async with async_session() as db:
        ns = Namespace(name="trainer_idem", slug="trainer_idem", description="t")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        enum_def = EnumDef(
            enum_class="PayStatus",
            fully_qualified_name="com.x.PayStatus",
            values=[EnumValue(name="UNPAID", db_value=0, description="未支付")],
        )

        # 第一次
        await upsert_enum_dictionary_from_code(db, ns.id, enum_def)
        await db.commit()

        # 第二次 (模拟 trainer 重跑)
        await upsert_enum_dictionary_from_code(db, ns.id, enum_def)
        await db.commit()

        rows = (
            await db.execute(
                select(EnumDictionary).where(
                    EnumDictionary.namespace_id == ns.id
                )
            )
        ).scalars().all()
        assert len(rows) == 1
