"""Stage 3 Task 3 — POST /api/knowledge/audit/batch 批量审核.

4 用例覆盖:
    1. 小批 [approve, approve, reject] → 全 success + 状态 + audit_log
    2. 超阈值缺 confirm_token → 422 (error=confirm_token_required + expected_token 字段)
    3. 超阈值正确 confirm_token → 200 + 全 success
    4. 单 action 状态机非法 → 全部 rollback (第一条 proposed 仍 proposed)
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import _compute_batch_confirm_token
from app.auth import get_current_user
from app.db.metadata import get_db
from app.main import app
from app.models import KnowledgeEntry, Namespace
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.user import User


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_audit_batch",
        password_hash="x",
        role="super_admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def http_client(
    db_session: AsyncSession, admin_user: User
) -> AsyncGenerator[AsyncClient, None]:
    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def ns(db_session: AsyncSession) -> Namespace:
    n = Namespace(name="ab_ns", slug="ab_ns", description="")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    return n


async def _make_entry(
    db: AsyncSession, ns_id: int, content: str, status: str = "proposed",
    entry_type: str = "terminology",
) -> KnowledgeEntry:
    e = KnowledgeEntry(
        namespace_id=ns_id,
        entry_type=entry_type,
        content=content,
        source="manual",
        status=status,
        tier="normal",
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


# ─────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_small_batch_approves_and_rejects_atomically(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """3 proposed → batch [approve, approve, reject], 全部成功且 audit 完整."""
    e1 = await _make_entry(db_session, ns.id, "term-1")
    e2 = await _make_entry(db_session, ns.id, "term-2")
    e3 = await _make_entry(db_session, ns.id, "term-3")

    r = await http_client.post(
        "/api/knowledge/audit/batch",
        json={
            "actions": [
                {"entry_id": e1.id, "action": "approve", "reason": "ok"},
                {"entry_id": e2.id, "action": "approve", "reason": "ok"},
                {"entry_id": e3.id, "action": "reject", "reason": "no"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["affected_count"] == 3
    assert sorted(body["success_ids"]) == sorted([e1.id, e2.id, e3.id])

    await db_session.refresh(e1)
    await db_session.refresh(e2)
    await db_session.refresh(e3)
    assert e1.status == "canonical"
    assert e2.status == "canonical"
    assert e3.status == "rejected"

    # 每条 entry 一条 audit_log
    for e, action in [(e1, "approve"), (e2, "approve"), (e3, "reject")]:
        logs = (await db_session.scalars(
            select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == e.id)
        )).all()
        assert len(logs) == 1, f"entry {e.id} expected 1 log got {len(logs)}"
        assert logs[0].action == action
        assert logs[0].actor_id == admin_user.id


@pytest.mark.asyncio
async def test_above_threshold_missing_confirm_token_returns_422(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
    monkeypatch,
) -> None:
    """阈值=1, 2 actions 缺 token → 422 + detail.error=confirm_token_required."""
    monkeypatch.setattr(
        "app.config.settings.bulk_op_require_confirm_above", 1
    )
    e1 = await _make_entry(db_session, ns.id, "x1")
    e2 = await _make_entry(db_session, ns.id, "x2")

    r = await http_client.post(
        "/api/knowledge/audit/batch",
        json={
            "actions": [
                {"entry_id": e1.id, "action": "approve"},
                {"entry_id": e2.id, "action": "approve"},
            ]
        },
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "confirm_token_required"
    assert detail["affected_count"] == 2
    assert detail["expected_token"] == _compute_batch_confirm_token(
        sorted([e1.id, e2.id]), 2
    )

    # DB 状态未变
    await db_session.refresh(e1)
    await db_session.refresh(e2)
    assert e1.status == "proposed"
    assert e2.status == "proposed"


@pytest.mark.asyncio
async def test_above_threshold_correct_token_executes(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
    monkeypatch,
) -> None:
    """阈值=1, 提交正确 token → 200 + 全 success."""
    monkeypatch.setattr(
        "app.config.settings.bulk_op_require_confirm_above", 1
    )
    e1 = await _make_entry(db_session, ns.id, "y1")
    e2 = await _make_entry(db_session, ns.id, "y2")

    sorted_ids = sorted([e1.id, e2.id])
    token = _compute_batch_confirm_token(sorted_ids, 2)

    r = await http_client.post(
        "/api/knowledge/audit/batch",
        json={
            "actions": [
                {"entry_id": e1.id, "action": "approve"},
                {"entry_id": e2.id, "action": "approve"},
            ],
            "confirm_token": token,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["affected_count"] == 2
    assert sorted(body["success_ids"]) == sorted_ids

    await db_session.refresh(e1)
    await db_session.refresh(e2)
    assert e1.status == "canonical"
    assert e2.status == "canonical"


@pytest.mark.asyncio
async def test_invalid_state_in_one_action_rolls_back_all(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """[proposed approve, canonical approve(非法)] → 422 + 全 rollback."""
    e_prop = await _make_entry(db_session, ns.id, "valid", status="proposed")
    e_canon = await _make_entry(db_session, ns.id, "already-canon", status="canonical")

    r = await http_client.post(
        "/api/knowledge/audit/batch",
        json={
            "actions": [
                {"entry_id": e_prop.id, "action": "approve"},
                {"entry_id": e_canon.id, "action": "approve"},
            ]
        },
    )
    assert r.status_code == 422, r.text
    assert "invalid_state" in r.json()["detail"]

    # 第一条 entry 状态回滚
    await db_session.refresh(e_prop)
    await db_session.refresh(e_canon)
    assert e_prop.status == "proposed"
    assert e_canon.status == "canonical"

    # 无 audit_log 写入
    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(
            KnowledgeAuditLog.entry_id.in_([e_prop.id, e_canon.id])
        )
    )).all()
    assert logs == []
