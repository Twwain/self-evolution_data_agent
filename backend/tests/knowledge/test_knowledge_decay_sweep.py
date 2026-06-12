"""Stage 2 抓手 B — 衰减 sweep 测试 (真实 DB).

使用与 test_hypothetical_queries.py 相同的 fixture 模式:
独立 sessionmaker + 唯一 namespace slug, 避免跨测试污染.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.jobs.knowledge_decay import sweep_once
from app.knowledge.audit import write_audit
from app.models import KnowledgeAuditLog, KnowledgeEntry, Namespace
from app.models.base import Base
from app.models.namespace import DataSource
from app.models.user import User

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


def _unique_slug() -> str:
    return f"decay_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def decay_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def decay_ns(decay_session) -> tuple[int, str]:
    """创建唯一 namespace + datasource, 返回 (ns_id, slug)."""
    slug = _unique_slug()
    async with decay_session() as db:
        ns = Namespace(name=slug, slug=slug, description="decay-test")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        ds = DataSource(
            namespace_id=ns.id, db_type="mongodb",
            database="db_decay", host="localhost", port=27017,
            username="", password="",
        )
        db.add(ds)
        await db.commit()
        return ns.id, slug


@pytest_asyncio.fixture
async def decay_admin(decay_session) -> User:
    """创建一个 admin 用户用于 human-audited 测试."""
    async with decay_session() as db:
        username = f"admin_{uuid.uuid4().hex[:8]}"
        user = User(username=username, password_hash="x", role="admin")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


@pytest.mark.asyncio
async def test_rule1_low_adoption_ratio_decays(
    decay_session, decay_ns, chroma_isolated, monkeypatch,
):
    """规则 1: recall_count > threshold AND adopted/recall < ratio → superseded."""
    ns_id, slug = decay_ns
    monkeypatch.setattr(settings, "kb_decay_recall_threshold", 5)
    monkeypatch.setattr(settings, "kb_decay_adoption_ratio", 0.1)
    monkeypatch.setattr(settings, "kb_decay_stale_days", 9999)  # 不触发规则 2

    async with decay_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id, entry_type="rule",
            content="low adoption", source="manual",
            status="canonical", tier="normal",
            recall_count=20, adopted_count=1, negative_signal_count=15,
            last_recalled_at=datetime.now(),
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)
        ke_id = ke.id

        # 清除该 entry 可能存在的历史 audit_log (防跨测试污染 — 共享 DB 中
        # 新 entry_id 可能复用了旧 ID, 旧 ID 可能有 approve audit)
        from sqlalchemy import delete
        await db.execute(
            delete(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == ke_id)
        )
        await db.commit()

    report = await sweep_once(session_factory=decay_session)
    assert report["rule1"] >= 1

    async with decay_session() as db:
        refreshed = await db.get(KnowledgeEntry, ke_id)
        assert refreshed is not None
        assert refreshed.status == "superseded"
        # 验证 audit_log
        audit = (await db.execute(
            select(KnowledgeAuditLog).where(
                KnowledgeAuditLog.entry_id == ke_id,
                KnowledgeAuditLog.action == "expire",
            )
        )).scalar_one()
        assert "decay" in audit.reason


@pytest.mark.asyncio
async def test_rule2_stale_days_decays(
    decay_session, decay_ns, chroma_isolated, monkeypatch,
):
    """规则 2: last_recalled_at 早于 stale_days → superseded."""
    ns_id, slug = decay_ns
    monkeypatch.setattr(settings, "kb_decay_recall_threshold", 9999)  # 不触发规则 1
    monkeypatch.setattr(settings, "kb_decay_stale_days", 90)

    old = datetime.now() - timedelta(days=120)
    async with decay_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id, entry_type="rule",
            content="stale entry", source="manual",
            status="canonical", tier="normal",
            recall_count=5, adopted_count=5,
            last_recalled_at=old,
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)
        ke_id = ke.id

    report = await sweep_once(session_factory=decay_session)
    assert report["rule2"] >= 1

    async with decay_session() as db:
        refreshed = await db.get(KnowledgeEntry, ke_id)
        assert refreshed is not None
        assert refreshed.status == "superseded"


@pytest.mark.asyncio
async def test_high_adoption_does_not_decay(
    decay_session, decay_ns, chroma_isolated, monkeypatch,
):
    """高采纳率条目不应被 sweep."""
    ns_id, slug = decay_ns
    monkeypatch.setattr(settings, "kb_decay_recall_threshold", 5)
    monkeypatch.setattr(settings, "kb_decay_adoption_ratio", 0.1)
    monkeypatch.setattr(settings, "kb_decay_stale_days", 9999)

    async with decay_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id, entry_type="rule",
            content="healthy entry", source="manual",
            status="canonical", tier="normal",
            recall_count=20, adopted_count=18, negative_signal_count=2,
            last_recalled_at=datetime.now(),
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)
        ke_id = ke.id

    await sweep_once(session_factory=decay_session)

    async with decay_session() as db:
        refreshed = await db.get(KnowledgeEntry, ke_id)
        assert refreshed is not None
        assert refreshed.status == "canonical", "高采纳率条目不应被 sweep"


@pytest.mark.asyncio
async def test_human_audited_preserved_from_decay(
    decay_session, decay_ns, decay_admin, chroma_isolated, monkeypatch,
):
    """D8 §3 人类编辑兜底 — actor_id != NULL ∧ action ∈ {approve, edit} 的 KE 不被 sweep."""
    ns_id, slug = decay_ns
    monkeypatch.setattr(settings, "kb_decay_recall_threshold", 5)
    monkeypatch.setattr(settings, "kb_decay_adoption_ratio", 0.1)
    monkeypatch.setattr(settings, "kb_decay_stale_days", 9999)

    async with decay_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id, entry_type="rule",
            content="audited entry", source="manual",
            status="canonical", tier="normal",
            recall_count=20, adopted_count=1,
            last_recalled_at=datetime.now(),
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)
        ke_id = ke.id

        # 人类 approve 过
        await write_audit(
            db, entry_id=ke.id, actor_id=decay_admin.id,
            action="approve", from_status="proposed", to_status="canonical",
            reason="human reviewed", diff={},
        )
        await db.commit()

    report = await sweep_once(session_factory=decay_session)

    async with decay_session() as db:
        refreshed = await db.get(KnowledgeEntry, ke_id)
        assert refreshed is not None
        assert refreshed.status == "canonical", "人类审过的 KE 不被 sweep 自动降级"
    assert report["preserved_audited"] >= 1


@pytest.mark.asyncio
async def test_dry_run_does_not_modify(
    decay_session, decay_ns, chroma_isolated, monkeypatch,
):
    """D8 §1 dry_run 模式 — 仅扫描不写."""
    ns_id, slug = decay_ns
    monkeypatch.setattr(settings, "kb_decay_recall_threshold", 5)
    monkeypatch.setattr(settings, "kb_decay_adoption_ratio", 0.1)
    monkeypatch.setattr(settings, "kb_decay_stale_days", 9999)

    async with decay_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id, entry_type="rule",
            content="dry run entry", source="manual",
            status="canonical", tier="normal",
            recall_count=20, adopted_count=1,
            last_recalled_at=datetime.now(),
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)
        ke_id = ke.id

    report = await sweep_once(dry_run=True, session_factory=decay_session)

    async with decay_session() as db:
        refreshed = await db.get(KnowledgeEntry, ke_id)
        assert refreshed is not None
        assert refreshed.status == "canonical", "dry_run 不应改 status"
    assert report["decayed"] == 0
    assert report["would_decay"] >= 1
