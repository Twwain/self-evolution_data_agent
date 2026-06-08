"""HyQE 上线后 rule 召回距离应明显小于 baseline (单向量 content embedding).

端到端测试: 同一条 rule, 分别在 HyQE 关/开 状态下入库并查询,
验证 HyQE 多向量召回距离 < 单向量 baseline.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.knowledge.knowledge_retriever import _retrieve_layer3, upsert_knowledge_entry
from app.models.base import Base
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace

from .conftest import TEST_DATABASE_URL


def _unique_slug() -> str:
    return f"hyqe_e2e_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def e2e_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    # 确保表结构与 ORM 一致 — drop + recreate 相关表
    async with engine.begin() as conn:
        # 仅 create_all (不 drop, 避免影响其他测试)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def e2e_ns(e2e_session) -> tuple[int, str]:
    slug = _unique_slug()
    async with e2e_session() as db:
        ns = Namespace(name=slug, slug=slug, description="hyqe-e2e")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        ds = DataSource(
            namespace_id=ns.id, db_type="mongodb",
            database="db_e2e", host="localhost", port=27017,
            username="", password="",
        )
        db.add(ds)
        await db.commit()
        return ns.id, slug


@pytest.mark.asyncio
async def test_hyqe_recall_distance_smaller_than_baseline(
    e2e_session, e2e_ns, chroma_isolated, monkeypatch,
):
    """HyQE 开启后, rule 召回距离应 < 关闭时的 baseline."""
    ns_id, slug = e2e_ns
    async with e2e_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id, entry_type="rule",
            content="活跃用户指 30 天内有过登录的用户",
            source="manual", status="canonical", tier="normal",
            is_superseded=False,
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)

    # ── baseline: 关 HyQE (单向量) ──
    monkeypatch.setattr(settings, "hypothetical_queries_enabled", False)
    upsert_knowledge_entry(
        slug=slug, entry_id=ke.id, content=ke.content,
        tier="normal", namespace_id=ke.namespace_id,
        entry_type="rule", status="canonical",
    )
    hits_off = _retrieve_layer3(
        slug, "本月活跃用户数",
        entry_types=["rule"], k_normal=5,
    )
    dist_off = next((h.distance for h in hits_off if h.entry_id == ke.id), 999)

    # ── 开 HyQE (多向量) ──
    monkeypatch.setattr(settings, "hypothetical_queries_enabled", True)
    upsert_knowledge_entry(
        slug=slug, entry_id=ke.id, content=ke.content,
        tier="normal", namespace_id=ke.namespace_id,
        entry_type="rule", status="canonical",
    )
    hits_on = _retrieve_layer3(
        slug, "本月活跃用户数",
        entry_types=["rule"], k_normal=5,
    )
    dist_on = next((h.distance for h in hits_on if h.entry_id == ke.id), 999)

    assert dist_on < dist_off, (
        f"HyQE 应降低召回距离: on={dist_on:.4f} off={dist_off:.4f}"
    )
