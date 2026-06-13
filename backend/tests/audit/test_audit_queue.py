"""Stage 3 Task 1 — GET /api/knowledge/audit/queue.

验收三态:
    1. 空库 → total=0, items=[], page=1, size=20
    2. 25 条 proposed + 分页 page=3,size=10 → 第三页只剩 5 条
    3. entry_type=terminology 过滤 → 仅 1 条命中, 排除 rule
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.metadata import get_db
from app.main import app
from app.models import KnowledgeEntry, Namespace
from app.models.user import User


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_audit_q",
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


# ─────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_queue_returns_zero_total(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    r = await http_client.get("/api/knowledge/audit/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["page"] == 1
    assert body["size"] == 20


@pytest.mark.asyncio
async def test_pagination_offset_correct(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    ns = Namespace(name="tq_pg", slug="tq_pg", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    for i in range(25):
        db_session.add(
            KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="terminology",
                content=f"t{i}",
                source="manual",
                status="proposed",
                tier="normal",
            )
        )
    await db_session.commit()

    r1 = await http_client.get("/api/knowledge/audit/queue?page=1&size=10")
    r2 = await http_client.get("/api/knowledge/audit/queue?page=3&size=10")
    assert r1.json()["total"] == 25
    assert len(r1.json()["items"]) == 10
    assert len(r2.json()["items"]) == 5


@pytest.mark.asyncio
async def test_entry_type_filter_isolates_terminology(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    ns = Namespace(name="tq_flt", slug="tq_flt", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    db_session.add_all(
        [
            KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="terminology",
                content="term_alpha",
                source="manual",
                status="proposed",
                tier="normal",
            ),
            KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="rule",
                content="rule_beta",
                source="manual",
                status="proposed",
                tier="normal",
            ),
        ]
    )
    await db_session.commit()

    r = await http_client.get(
        "/api/knowledge/audit/queue?entry_type=terminology"
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["entry_type"] == "terminology"


@pytest.mark.asyncio
async def test_explicit_status_proposed_filters_canonical_out(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """显式 status=proposed: canonical 条目应被过滤 (锁住精确过滤语义)."""
    ns = Namespace(name="def_st", slug="def_st", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add_all([
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="terminology", content="prop",
            source="manual", status="proposed", tier="normal",
        ),
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="terminology", content="canon",
            source="manual", status="canonical", tier="normal",
        ),
    ])
    await db_session.commit()

    r = await http_client.get(
        "/api/knowledge/audit/queue", params={"status": "proposed"}
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["content"] == "prop"


# ─────────────────────────────────────────────────────────────────
# q (search keyword) 维度测试 — 2026-05-07
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_q_match_content(
    db_session: AsyncSession, http_client: AsyncClient
) -> None:
    ns = Namespace(name="ns_q_content", slug="ns-q-content")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add_all([
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="rule", tier="normal",
            content="GMV 是销售额", payload="{}", description="",
            source="manual", status="proposed",
        ),
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="rule", tier="normal",
            content="DAU 是日活", payload="{}", description="",
            source="manual", status="proposed",
        ),
    ])
    await db_session.commit()

    resp = await http_client.get(
        "/api/knowledge/audit/queue",
        params={"namespace_id": ns.id, "q": "GMV"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert "GMV" in body["items"][0]["content"]


@pytest.mark.asyncio
async def test_queue_q_match_description_case_insensitive(
    db_session: AsyncSession, http_client: AsyncClient
) -> None:
    ns = Namespace(name="ns_q_desc", slug="ns-q-desc")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add(KnowledgeEntry(
        namespace_id=ns.id, entry_type="rule", tier="normal",
        content="x", payload="{}", description="Hello World",
        source="manual", status="proposed",
    ))
    await db_session.commit()

    resp = await http_client.get(
        "/api/knowledge/audit/queue",
        params={"namespace_id": ns.id, "q": "WORLD"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_queue_q_match_payload(
    db_session: AsyncSession, http_client: AsyncClient
) -> None:
    ns = Namespace(name="ns_q_payload", slug="ns-q-payload")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add(KnowledgeEntry(
        namespace_id=ns.id, entry_type="terminology", tier="normal",
        content="term entry", description="",
        payload='{"term": "ARPU", "synonyms": ["每用户平均收入"]}',
        source="manual", status="proposed",
    ))
    await db_session.commit()

    resp = await http_client.get(
        "/api/knowledge/audit/queue",
        params={"namespace_id": ns.id, "q": "ARPU"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_queue_q_combined_with_entry_type(
    db_session: AsyncSession, http_client: AsyncClient
) -> None:
    ns = Namespace(name="ns_q_and", slug="ns-q-and")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add_all([
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="rule", tier="normal",
            content="销售额规则", payload="{}", description="",
            source="manual", status="proposed",
        ),
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="example", tier="normal",
            content="销售额示例", payload="{}", description="",
            source="manual", status="proposed",
        ),
    ])
    await db_session.commit()

    resp = await http_client.get(
        "/api/knowledge/audit/queue",
        params={"namespace_id": ns.id, "q": "销售额", "entry_type": "rule"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["entry_type"] == "rule"


@pytest.mark.asyncio
async def test_queue_q_no_match(
    db_session: AsyncSession, http_client: AsyncClient
) -> None:
    ns = Namespace(name="ns_q_miss", slug="ns-q-miss")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add(KnowledgeEntry(
        namespace_id=ns.id, entry_type="rule", tier="normal",
        content="hello", payload="{}", description="",
        source="manual", status="proposed",
    ))
    await db_session.commit()

    resp = await http_client.get(
        "/api/knowledge/audit/queue",
        params={"namespace_id": ns.id, "q": "nonexistent_token_xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_queue_status_none_returns_all_status(
    db_session: AsyncSession, http_client: AsyncClient
) -> None:
    """status 不传 (None) → 返回所有 status 行 (proposed + canonical + rejected)."""
    ns = Namespace(name="ns_status_all", slug="ns-status-all")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    db_session.add_all([
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="rule", tier="normal",
            content="proposed entry", payload="{}", description="",
            source="manual", status="proposed",
        ),
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="rule", tier="normal",
            content="canonical entry", payload="{}", description="",
            source="manual", status="canonical",
        ),
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="rule", tier="normal",
            content="rejected entry", payload="{}", description="",
            source="manual", status="rejected",
        ),
    ])
    await db_session.commit()

    resp = await http_client.get(
        "/api/knowledge/audit/queue",
        params={"namespace_id": ns.id},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 3
