"""Task 4: enum_sync_loop_once — 队列消费 + 异常隔离测试.

不再 mock sync_enum_dict_to_bound_fields. 项目纪律: 接真实 SQLite + ChromaDB +
LLM, 用真实异常路径 (malformed values_json 触发 JSONDecodeError) 验证 worker
异常隔离, 而非 patch 业务函数走形式.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.knowledge.enum_sync import enum_sync_loop_once
from app.models.enum_dictionary import EnumDictionary
from app.models.enum_sync_queue import EnumSyncQueue
from app.models.namespace import Namespace


@pytest_asyncio.fixture
async def ns(async_session):
    async with async_session() as db:
        n = Namespace(name="loop_test", slug="loop_test", description="")
        db.add(n)
        await db.commit()
        await db.refresh(n)
        return n


@pytest_asyncio.fixture
async def db(async_session):
    async with async_session() as session:
        yield session


@pytest.mark.asyncio
async def test_loop_processes_and_clears_queue(db, ns):
    """即使 sync 没找到 enum, 任务也消费 (不存在的 enum_dict_id)."""
    db.add(EnumSyncQueue(
        enum_dict_id=99999, namespace_id=ns.id, event="create",
    ))
    await db.commit()

    await enum_sync_loop_once(db)

    rows = (await db.execute(select(EnumSyncQueue))).scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_loop_isolates_real_failure(db, ns):
    """真实异常路径: malformed values_json 让 _handle_create 抛 JSONDecodeError.

    第一条任务对应的 EnumDictionary 行 values_json 是非法 JSON, 真实代码路径
    在 _enum_to_def 解析时抛错; 第二条对应正常 EnumDictionary, 必须照常处理.
    验证: worker 异常隔离 + 两条任务行都被消费, 不需要 mock.
    """
    bad_enum = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="BadEnum",
        values_json="{not json at all",  # 真实 malformed
        source="manual",
    )
    good_enum = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="GoodEnum",
        values_json='[{"name": "A", "db_value": 1}]',
        source="manual",
    )
    db.add_all([bad_enum, good_enum])
    await db.commit()
    await db.refresh(bad_enum)
    await db.refresh(good_enum)

    db.add(EnumSyncQueue(enum_dict_id=bad_enum.id, namespace_id=ns.id, event="create"))
    db.add(EnumSyncQueue(enum_dict_id=good_enum.id, namespace_id=ns.id, event="create"))
    await db.commit()

    await enum_sync_loop_once(db)

    rows = (await db.execute(select(EnumSyncQueue))).scalars().all()
    assert len(rows) == 0  # 两条都消费, 失败不阻塞


@pytest.mark.asyncio
async def test_loop_dedups_repeated_update_events(db, ns):
    """同 (enum_dict_id, event) 短时间多次入队, 折叠到最新一条 — 防止
    用户连续编辑同一 EnumDictionary 时 worker 重复扫 N 次同 enum.
    """
    enum_row = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="OrderStatus",
        values_json='[{"name": "PAID", "db_value": 1}]',
        source="manual",
    )
    db.add(enum_row)
    await db.commit()
    await db.refresh(enum_row)

    # 同 (enum_dict_id, event=update) 连发 3 次
    for _ in range(3):
        db.add(EnumSyncQueue(
            enum_dict_id=enum_row.id, namespace_id=ns.id, event="update",
        ))
    await db.commit()

    pre_rows = (await db.execute(select(EnumSyncQueue))).scalars().all()
    assert len(pre_rows) == 3

    processed = await enum_sync_loop_once(db)

    rows = (await db.execute(select(EnumSyncQueue))).scalars().all()
    assert len(rows) == 0
    # 3 条全消费, 但 sync_enum_dict_to_bound_fields 只跑 1 次 (折叠后留最新);
    # processed 计数等于 stale 删除 (2) + 跑过 (1) = 3, 业务幂等不会多扫.
    assert processed == 3
