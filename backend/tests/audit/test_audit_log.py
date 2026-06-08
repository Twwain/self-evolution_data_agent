"""KnowledgeAuditLog 写入 + 查询 — 真实 SQLite."""

import pytest

from app.knowledge.audit import list_audit_logs, write_audit
from app.models.knowledge_entry import KnowledgeEntry


@pytest.mark.asyncio
async def test_write_audit_basic(db_session):
    entry = KnowledgeEntry(
        entry_type="terminology",
        content="条目",
        source="manual",
        status="proposed",
    )
    db_session.add(entry)
    await db_session.flush()

    await write_audit(
        db_session,
        entry_id=entry.id,
        action="propose",
        from_status=None,
        to_status="proposed",
        actor_id=None,
        reason="agent learned",
    )
    await db_session.commit()

    logs = await list_audit_logs(db_session, entry_id=entry.id)
    assert len(logs) == 1
    assert logs[0].action == "propose"
    assert logs[0].to_status == "proposed"
    assert logs[0].actor_id is None


@pytest.mark.asyncio
async def test_write_audit_with_diff(db_session):
    entry = KnowledgeEntry(
        entry_type="rule",
        content="x",
        source="manual",
        status="canonical",
    )
    db_session.add(entry)
    await db_session.flush()

    await write_audit(
        db_session,
        entry_id=entry.id,
        action="edit",
        from_status="canonical",
        to_status="canonical",
        actor_id=42,
        reason="fix typo",
        diff={"content": {"before": "x", "after": "y"}},
    )
    await db_session.commit()

    logs = await list_audit_logs(db_session, entry_id=entry.id)
    assert logs[0].actor_id == 42
    assert "before" in logs[0].diff_json


@pytest.mark.asyncio
async def test_list_audit_logs_chronological_order(db_session):
    """多条 audit 按 created_at 升序返回 — review UI 时间线依赖此顺序."""
    entry = KnowledgeEntry(
        entry_type="rule", content="x", source="manual", status="proposed",
    )
    db_session.add(entry)
    await db_session.flush()

    await write_audit(db_session, entry_id=entry.id, action="propose",
                       to_status="proposed")
    await write_audit(db_session, entry_id=entry.id, action="approve",
                       from_status="proposed", to_status="canonical")
    await write_audit(db_session, entry_id=entry.id, action="edit",
                       from_status="canonical", to_status="canonical",
                       diff={"k": "v"})
    await db_session.commit()

    logs = await list_audit_logs(db_session, entry_id=entry.id)
    assert [l.action for l in logs] == ["propose", "approve", "edit"]


@pytest.mark.asyncio
async def test_chinese_reason_no_ascii_escape(db_session):
    """中文 reason / diff 写入 ensure_ascii=False, 不被转义为 \\uXXXX."""
    entry = KnowledgeEntry(
        entry_type="terminology", content="订单", source="manual", status="proposed",
    )
    db_session.add(entry)
    await db_session.flush()

    await write_audit(
        db_session, entry_id=entry.id, action="reject", to_status="rejected",
        reason="术语命名冲突",
        diff={"原值": "订单", "建议": "单子"},
    )
    await db_session.commit()

    log = (await list_audit_logs(db_session, entry_id=entry.id))[0]
    assert log.reason == "术语命名冲突"
    assert "原值" in log.diff_json
    assert "\\u" not in log.diff_json
