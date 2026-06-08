"""Stage 3 Task 5 — PUT /api/knowledge/{id} 升级版编辑端点.

4 用例覆盖:
    1. proposed entry 编辑 content → 200 + audit_log(edit) diff 含 before/after
    2. payload 走 parse_payload Pydantic 校验 — 合法 200, 非法 422
    3. canonical entry 编辑 content → 200, response.conflicts 字段存在 (Task 5 stub 返 [])
    4. rejected entry 编辑 → 422 invalid_state_transition
"""

import json
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db.metadata import get_db
from app.main import app
from app.models import KnowledgeEntry, Namespace
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.user import User


# ─────────────────────────────────────────────────────────────────
# Fixtures (照抄 test_audit_actions.py 同名 pattern, 保持隔离)
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_edit_canonical",
        password_hash="x",
        role="admin",
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
    app.dependency_overrides[require_admin] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def ns(db_session: AsyncSession) -> Namespace:
    n = Namespace(name="ec_ns", slug="ec_ns", description="")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    return n


# ─────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_content_writes_audit_with_diff(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """proposed entry 编辑 content — audit_log 落 diff{before, after}, reason 留痕."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="old",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.put(
        f"/api/knowledge/{entry.id}",
        json={"content": "new", "reason": "fix typo"},
    )
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["entry"]["content"] == "new"
    assert body["entry"]["status"] == "proposed"  # status 不变

    await db_session.refresh(entry)
    assert entry.content == "new"

    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == entry.id)
    )).all()
    assert len(logs) == 1
    log_row = logs[0]
    assert log_row.action == "edit"
    assert log_row.reason == "fix typo"
    assert log_row.actor_id == admin_user.id
    assert log_row.from_status == "proposed"
    assert log_row.to_status == "proposed"

    diff = json.loads(log_row.diff_json)
    assert diff["before"]["content"] == "old"
    assert diff["after"]["content"] == "new"


@pytest.mark.asyncio
async def test_edit_payload_validates_schema(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """payload 走 parse_payload — terminology 合法字段 200, 非法字段 422."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="订单",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    # ── 合法 payload (TerminologyPayload schema) ──
    r_ok = await http_client.put(
        f"/api/knowledge/{entry.id}",
        json={
            "payload": {
                "term": "订单", "synonyms": ["order", "exam"],
                "primary_collection": "c_product",
                "primary_database": "db_test",
                "db_type": "mongodb",
            },
            "reason": "补 synonyms",
        },
    )
    assert r_ok.status_code == 200, r_ok.text

    await db_session.refresh(entry)
    stored = json.loads(entry.payload)
    assert stored["term"] == "订单"
    assert stored["synonyms"] == ["order", "exam"]

    # ── 非法 payload (extra="forbid" 拒绝 invalid_field) ──
    r_bad = await http_client.put(
        f"/api/knowledge/{entry.id}",
        json={
            "payload": {"invalid_field": "x"},
            "reason": "试错",
        },
    )
    assert r_bad.status_code == 422, r_bad.text


@pytest.mark.asyncio
async def test_edit_canonical_returns_conflicts_field(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """canonical entry 编辑后, response.conflicts 字段必须存在 (Task 7 落地真实数据)."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="canonical-old",
        source="manual",
        status="canonical",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.put(
        f"/api/knowledge/{entry.id}",
        json={"content": "canonical-new", "reason": "校对"},
    )
    assert r.status_code == 200, r.text

    body = r.json()
    assert "conflicts" in body
    assert isinstance(body["conflicts"], list)
    # Task 5 stub 阶段必返 []; Task 7 替换 LLM 后会有候选
    assert body["conflicts"] == []

    await db_session.refresh(entry)
    assert entry.content == "canonical-new"
    assert entry.status == "canonical"


@pytest.mark.asyncio
async def test_edit_rejected_returns_422(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """rejected 状态禁止编辑 — 422 invalid_state_transition."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="已拒",
        source="manual",
        status="rejected",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.put(
        f"/api/knowledge/{entry.id}",
        json={"content": "尝试改", "reason": "x"},
    )
    assert r.status_code == 422
    assert "invalid_state_transition" in r.text
