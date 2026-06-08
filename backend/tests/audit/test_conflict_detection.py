"""Stage 3 Task 7 — POST /api/knowledge/audit/conflict-preview + LLM 冲突检测落地.

3 用例覆盖:
    1. 空候选集 (无 canonical 同 type) → 200 conflicts=[]
    2. 真 LLM 比对同义内容 → 200, conflicts list 非空 (LLM 应判同义/重叠)
    3. exclude_entry_id 防自比 → 1 条 canonical 用其 id 排除 → 200 conflicts=[]

LLM 真调用通过 IS_LLM_API_KEY skipif 控制 — CI 缺 key 时跳过, 本地有 key 时真测.
"""

import os
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db.metadata import get_db
from app.knowledge.intake import ConflictItem, ConflictReport
from app.main import app
from app.models import KnowledgeEntry, Namespace
from app.models.user import User

# ─────────────────────────────────────────────────────────────────
# Fixtures (照抄 test_edit_canonical.py 隔离模式)
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_conflict_preview",
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
    n = Namespace(name="cp_ns", slug="cp_ns", description="")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    return n


# ─────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_canonical_returns_empty(
    http_client: AsyncClient,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """空候选集 — 无 canonical 同 type, 接口必返 conflicts=[] 且不调 LLM."""
    # patch 下层 detect_conflicts 验证它确实没被调 (空 existing 短路)
    with patch("app.knowledge.audit.detect_conflicts") as mock_llm:
        r = await http_client.post(
            "/api/knowledge/audit/conflict-preview",
            json={
                "namespace_id": ns.id,
                "entry_type": "terminology",
                "content": "无候选场景, 直接返空",
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conflicts"] == []
    # 候选集为空时, audit 模块应短路, 不调 LLM
    mock_llm.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("IS_LLM_API_KEY"),
    reason="LLM key missing — 真 LLM 调用跳过 (本地配 IS_LLM_API_KEY 后启用)",
)
async def test_conflict_detected_via_real_llm(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """真 LLM — 同义术语条目应被判出冲突, conflicts 列表非空."""
    canonical = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="学生A的姓名映射到 t_student.name 字段",
        source="manual",
        status="canonical",
        tier="normal",
    )
    db_session.add(canonical)
    await db_session.commit()
    await db_session.refresh(canonical)

    r = await http_client.post(
        "/api/knowledge/audit/conflict-preview",
        json={
            "namespace_id": ns.id,
            "entry_type": "terminology",
            "content": "学生A的名字对应 t_student.name",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    # LLM 应判同义/重叠 — 至少 1 条冲突指向 canonical.id
    assert len(body["conflicts"]) >= 1
    existing_ids = [c["existing_id"] for c in body["conflicts"]]
    assert canonical.id in existing_ids


@pytest.mark.asyncio
async def test_exclude_entry_id_prevents_self_comparison(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """编辑场景 — entry_id 给定时排除自身, 候选集为空, LLM 不被调用."""
    canonical = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="GMV = 含退款总成交额",
        source="manual",
        status="canonical",
        tier="normal",
    )
    db_session.add(canonical)
    await db_session.commit()
    await db_session.refresh(canonical)

    # patch 下层验证排除后候选为空, LLM 短路不被调
    with patch(
        "app.knowledge.audit.detect_conflicts",
        return_value=ConflictReport(items=[]),
    ) as mock_llm:
        r = await http_client.post(
            "/api/knowledge/audit/conflict-preview",
            json={
                "namespace_id": ns.id,
                "entry_type": "terminology",
                "content": "GMV = 含退款总成交额 (微调表述)",
                "entry_id": canonical.id,
            },
        )

    assert r.status_code == 200, r.text
    assert r.json()["conflicts"] == []
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_conflict_preview_maps_llm_items_to_response(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """LLM 返 ConflictItem 列表时, 端点应原样映射为 ConflictItemOut JSON.

    用 mock 隔离 LLM 不稳定性, 仅验证字段映射 + 端点契约.
    """
    canonical = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="status=1 表示已支付",
        source="manual",
        status="canonical",
        tier="normal",
    )
    db_session.add(canonical)
    await db_session.commit()
    await db_session.refresh(canonical)

    fake_report = ConflictReport(items=[
        ConflictItem(existing_id=canonical.id, reason="语义重叠", suggested="merge"),
    ])
    with patch("app.knowledge.audit.detect_conflicts", return_value=fake_report):
        r = await http_client.post(
            "/api/knowledge/audit/conflict-preview",
            json={
                "namespace_id": ns.id,
                "entry_type": "rule",
                "content": "已支付状态对应 status=1",
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["conflicts"]) == 1
    item = body["conflicts"][0]
    assert item["existing_id"] == canonical.id
    assert item["reason"] == "语义重叠"
    assert item["suggested"] == "merge"
