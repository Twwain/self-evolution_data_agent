"""Task 2: field_description 分支 enum 绑定逻辑测试.

测试 promote 时 field_description candidate 携带 enum_class_hint:
- EnumDictionary 存在 → matched + snapshot
- EnumDictionary 不存在 → pending
- 无 hint 但 enum_match_status=pending → 仅写 pending
"""
import json
import os

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.models.base import Base
from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace
from app.models.schema_canonical_candidate import SchemaCanonicalCandidate
from app.models.schema_canonical_object import SchemaCanonicalObject

pytestmark = pytest.mark.asyncio

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


@pytest_asyncio.fixture
async def async_db():
    """独立 PostgreSQL session."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def ns(async_db: AsyncSession):
    """创建测试 namespace."""
    ns = Namespace(slug="test_enum_bind", name="Test Enum Bind")
    async_db.add(ns)
    await async_db.commit()
    await async_db.refresh(ns)
    return ns


async def test_field_binds_when_enum_dict_exists(async_db: AsyncSession, ns: Namespace):
    """enum_class_hint 命中 EnumDictionary → matched + snapshot."""
    enum_dict = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="OrderStatus",
        values_json='[{"name":"CREATED","db_value":1}]',
        source="code",
    )
    async_db.add(enum_dict)
    await async_db.commit()
    await async_db.refresh(enum_dict)

    cand = SchemaCanonicalCandidate(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db1",
        target="orders",
        field_path="status",
        candidate_kind="field_description",
        candidate_value_json=json.dumps({
            "description": "订单状态",
            "enum_class_hint": "OrderStatus",
            "enum_source": "code_hint",
        }),
        value_hash="abc",
        status="pending",
    )
    async_db.add(cand)
    await async_db.commit()

    await promote_candidates_to_canonical(async_db, ns.id)
    await async_db.commit()

    sco = (await async_db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.target == "orders",
        )
    )).scalar_one()
    fields = json.loads(sco.fields_json)
    status_field = next(f for f in fields if f.get("name") == "status")
    assert status_field["enum_ref_id"] == enum_dict.id
    assert status_field["enum_match_status"] == "matched"
    assert status_field["enum_source"] == "code_hint"
    assert status_field["enum_values"] == [{"name": "CREATED", "db_value": 1}]
    assert status_field["description"] == "订单状态"


async def test_field_pending_when_enum_dict_missing(async_db: AsyncSession, ns: Namespace):
    """enum_class_hint 未命中 EnumDictionary → pending."""
    cand = SchemaCanonicalCandidate(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db1",
        target="docs",
        field_path="moduleType",
        candidate_kind="field_description",
        candidate_value_json=json.dumps({
            "description": "module type",
            "enum_class_hint": "GoneEnum",
            "enum_source": "code_hint",
        }),
        value_hash="def",
        status="pending",
    )
    async_db.add(cand)
    await async_db.commit()

    await promote_candidates_to_canonical(async_db, ns.id)
    await async_db.commit()

    sco = (await async_db.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.target == "docs")
    )).scalar_one()
    fields = json.loads(sco.fields_json)
    f = next(x for x in fields if x.get("name") == "moduleType")
    assert f["enum_match_status"] == "pending"
    assert f["enum_class_hint"] == "GoneEnum"
    assert f.get("enum_ref_id") is None


async def test_field_pending_when_no_hint_only_status(async_db: AsyncSession, ns: Namespace):
    """无 enum_class_hint 但 payload 含 enum_match_status=pending → 仅写 pending."""
    cand = SchemaCanonicalCandidate(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db1",
        target="items",
        field_path="someStatus",
        candidate_kind="field_description",
        candidate_value_json=json.dumps({
            "description": "some status",
            "enum_match_status": "pending",
        }),
        value_hash="ghi",
        status="pending",
    )
    async_db.add(cand)
    await async_db.commit()

    await promote_candidates_to_canonical(async_db, ns.id)
    await async_db.commit()

    sco = (await async_db.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.target == "items")
    )).scalar_one()
    fields = json.loads(sco.fields_json)
    f = next(x for x in fields if x.get("name") == "someStatus")
    assert f["enum_match_status"] == "pending"
    assert "enum_class_hint" not in f
    assert f["description"] == "some status"
