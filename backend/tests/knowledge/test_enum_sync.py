"""Task 2-3: sync_enum_dict_to_bound_fields — create/update/delete 事件测试."""
import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.knowledge.enum_sync import sync_enum_dict_to_bound_fields
from app.models.enum_binding_conflict import EnumBindingConflict
from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace
from app.models.schema_canonical_object import SchemaCanonicalObject


@pytest_asyncio.fixture
async def ns(async_session):
    async with async_session() as db:
        n = Namespace(name="enum_sync", slug="enum_sync", description="")
        db.add(n)
        await db.commit()
        await db.refresh(n)
        return n


@pytest_asyncio.fixture
async def db(async_session):
    async with async_session() as session:
        yield session


# ════════════════════════════════════════════
#  Task 2: create 事件 (含启发式 rebind)
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_rebinds_pending_with_hint(db, ns):
    """pending 字段含 enum_class_hint 命中 EnumDictionary 名 → bind."""
    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mongodb", database="db1", target="t",
        fields_json=json.dumps([{
            "name": "isDeleted", "type": "Integer",
            "enum_class_hint": "DeleteStatus",
            "enum_match_status": "pending",
        }]),
    )
    e = EnumDictionary(
        namespace_id=ns.id, enum_class_name="DeleteStatus",
        values_json='[{"name":"NORMAL","db_value":0}]',
    )
    db.add_all([sco, e])
    await db.commit()

    rep = await sync_enum_dict_to_bound_fields(db, e.id, event="create")
    assert rep["rebound"] >= 1

    await db.refresh(sco)
    f = json.loads(sco.fields_json)[0]
    assert f["enum_match_status"] == "matched"
    assert f["enum_ref_id"] == e.id
    assert f["enum_source"] == "code_hint"


@pytest.mark.asyncio
async def test_create_rebinds_pending_without_hint_via_heuristic(db, ns):
    """pending 字段无 hint, 通过启发式词根匹配 EnumDictionary → bind."""
    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mongodb", database="db1", target="t2",
        fields_json=json.dumps([{
            "name": "orderStatus", "type": "Integer",
            "enum_match_status": "pending",
        }]),
    )
    e = EnumDictionary(
        namespace_id=ns.id, enum_class_name="OrderStatus",
        values_json='[{"name":"CREATED","db_value":1}]',
    )
    db.add_all([sco, e])
    await db.commit()

    rep = await sync_enum_dict_to_bound_fields(db, e.id, event="create")
    assert rep["rebound"] >= 1

    await db.refresh(sco)
    f = json.loads(sco.fields_json)[0]
    assert f["enum_match_status"] == "matched"
    assert f["enum_ref_id"] == e.id
    assert f["enum_source"] == "name_heuristic"


@pytest.mark.asyncio
async def test_create_skips_when_no_hint_and_root_mismatch(db, ns):
    """字段词根与 enum 词根不等 → 不 bind."""
    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mongodb", database="db1", target="t3",
        fields_json=json.dumps([{
            "name": "moduleType", "type": "Integer",
            "enum_match_status": "pending",
        }]),
    )
    e = EnumDictionary(
        namespace_id=ns.id, enum_class_name="ResourceType",
        values_json='[{"name":"X","db_value":1}]',
    )
    db.add_all([sco, e])
    await db.commit()

    rep = await sync_enum_dict_to_bound_fields(db, e.id, event="create")
    assert rep["rebound"] == 0


@pytest.mark.asyncio
async def test_create_idempotent(db, ns):
    """重复跑 create 事件, 已 matched 字段不重复计数."""
    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mongodb", database="db1", target="t4",
        fields_json=json.dumps([{
            "name": "isDeleted", "type": "Integer",
            "enum_class_hint": "DeleteStatus",
            "enum_match_status": "pending",
        }]),
    )
    e = EnumDictionary(
        namespace_id=ns.id, enum_class_name="DeleteStatus",
        values_json='[{"name":"A","db_value":0}]',
    )
    db.add_all([sco, e])
    await db.commit()

    r1 = await sync_enum_dict_to_bound_fields(db, e.id, event="create")
    r2 = await sync_enum_dict_to_bound_fields(db, e.id, event="create")
    assert r1["rebound"] == 1
    assert r2["rebound"] == 0


# ════════════════════════════════════════════
#  Task 3: update + delete 事件
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_no_sample_no_conflict(db, ns):
    """字段无 sample_values, update 仅刷 snapshot, 不进 conflict."""
    e = EnumDictionary(
        namespace_id=ns.id, enum_class_name="X",
        values_json='[{"name":"A","db_value":1}]',
    )
    db.add(e)
    await db.commit()

    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mongodb", database="db1", target="t5",
        fields_json=json.dumps([{
            "name": "f", "enum_ref_id": e.id,
            "enum_values": [{"name": "A", "db_value": 1}],
            "enum_match_status": "matched",
        }]),
    )
    db.add(sco)
    await db.commit()

    # 修改 enum
    e.values_json = '[{"name":"A","db_value":1},{"name":"B","db_value":2}]'
    await db.commit()

    rep = await sync_enum_dict_to_bound_fields(db, e.id, event="update")
    assert rep["conflicts"] == 0
    await db.refresh(sco)
    f = json.loads(sco.fields_json)[0]
    assert len(f["enum_values"]) == 2
    assert f["enum_match_status"] == "matched"


@pytest.mark.asyncio
async def test_update_sample_uncovered_creates_conflict(db, ns):
    """sample 含未覆盖值 → conflict."""
    e = EnumDictionary(
        namespace_id=ns.id, enum_class_name="Y",
        values_json='[{"name":"A","db_value":1}]',
    )
    db.add(e)
    await db.commit()

    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mongodb", database="db1", target="t6",
        fields_json=json.dumps([{
            "name": "f", "enum_ref_id": e.id,
            "sample_values": [1, 2],
            "enum_match_status": "matched",
        }]),
    )
    db.add(sco)
    await db.commit()

    rep = await sync_enum_dict_to_bound_fields(db, e.id, event="update")
    assert rep["conflicts"] == 1
    await db.refresh(sco)
    f = json.loads(sco.fields_json)[0]
    assert f["enum_match_status"] == "conflict"

    # 验证 conflict 行写入
    conflicts = (await db.execute(
        select(EnumBindingConflict).where(
            EnumBindingConflict.enum_dict_id == e.id,
        )
    )).scalars().all()
    assert len(conflicts) == 1
    assert conflicts[0].status == "open"


@pytest.mark.asyncio
async def test_delete_cascade(db, ns):
    """delete 事件 → unbind, 字段名含后缀 → pending."""
    e = EnumDictionary(
        namespace_id=ns.id, enum_class_name="Z",
        values_json='[{"name":"A","db_value":1}]',
    )
    db.add(e)
    await db.commit()
    eid = e.id

    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mongodb", database="db1", target="t7",
        fields_json=json.dumps([{
            "name": "deleteStatus", "enum_ref_id": eid,
            "enum_values": [{"name": "A", "db_value": 1}],
            "enum_source": "name_heuristic",
            "enum_match_status": "matched",
        }]),
    )
    db.add(sco)
    await db.commit()

    await db.delete(e)
    await db.commit()

    rep = await sync_enum_dict_to_bound_fields(
        db, eid, event="delete", namespace_id=ns.id,
    )
    assert rep["unbound"] >= 1
    await db.refresh(sco)
    f = json.loads(sco.fields_json)[0]
    assert "enum_ref_id" not in f
    assert "enum_values" not in f
    assert f["enum_match_status"] == "pending"
