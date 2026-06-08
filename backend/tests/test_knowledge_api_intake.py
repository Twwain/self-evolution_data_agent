"""
KnowledgeEntry 录入 API — 接入 intake 流水线的契约测试

Phase 1c Task 1.5: terminology 通道改走 upsert_terminology_with_validation 闸门,
不再过 refine→conflict 路径; 这些 refine/conflict/overflow 用例改用 entry_type=rule
覆盖 5 类宪章中走既有路径的 4 类 (rule/example/route_hint/schema_summary).

覆盖:
- 201 正常录入 (refine + 无冲突)
- 201 有冲突 (非阻断, 随 entry 一起返回)
- 409 overflow + split_candidates 正常返回
- 409 overflow + propose_split 失败 → split_candidates=[]
- 409 overflow + 过滤仍超长的 split 候选
- 404 namespace 不存在
"""

from unittest.mock import patch

import pytest

from app.models import KnowledgeEntry, Namespace


# ─────────────────────────── stub 工厂 ───────────────────────────

def _refine_result(refined: str, description: str = "", overflow: bool = False):
    return type("R", (), {"refined": refined, "description": description, "overflow": overflow})()


def _conflict_item(existing_id: int, reason: str = "", suggested: str = "merge"):
    return type("I", (), {"existing_id": existing_id, "reason": reason, "suggested": suggested})()


def _conflict_report(items):
    return type("C", (), {"items": items})()


# ─────────────────────────── 测试用例 ───────────────────────────

@pytest.mark.asyncio
async def test_create_knowledge_success(db, admin_client):
    """正常录入: refine 成功 + 无冲突 → 201, entry 字段齐全"""
    ns = Namespace(name="t", slug="t")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    with patch("app.api.knowledge.refine_knowledge",
               return_value=_refine_result("GMV = 含退款总成交额", "GMV 口径")), \
         patch("app.api.knowledge.detect_conflicts",
               return_value=_conflict_report([])):
        r = await admin_client.post("/api/knowledge", json={
            "entry_type": "rule",
            "content": "GMV 就是那个成交额",
            "namespace_id": ns.id,
            "tier": "normal",
        })

    assert r.status_code == 201
    body = r.json()
    assert body["entry"]["tier"] == "normal"
    assert body["entry"]["description"] == "GMV 口径"
    assert body["entry"]["content"] == "GMV = 含退款总成交额"
    assert body["entry"]["raw_input"] == "GMV 就是那个成交额"
    assert body["conflicts"] == []
    assert body["overflow"] is False


@pytest.mark.asyncio
async def test_create_knowledge_returns_conflicts(db, admin_client):
    """有冲突条目: 非阻断, 随 201 一起返回 conflicts 列表"""
    ns = Namespace(name="t", slug="t")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    db.add(KnowledgeEntry(
        namespace_id=ns.id, entry_type="rule", content="有效订单指 status=1",
    ))
    await db.commit()

    with patch("app.api.knowledge.refine_knowledge",
               return_value=_refine_result("有效订单指 status=2", "口径")), \
         patch("app.api.knowledge.detect_conflicts",
               return_value=_conflict_report([_conflict_item(1, "取值不同", "merge")])):
        r = await admin_client.post("/api/knowledge", json={
            "entry_type": "rule",
            "content": "有效订单指 status=2",
            "namespace_id": ns.id,
            "tier": "critical",
        })

    assert r.status_code == 201
    body = r.json()
    assert body["entry"]["tier"] == "critical"
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["suggested"] == "merge"


@pytest.mark.asyncio
async def test_create_knowledge_overflow_returns_split_candidates(db, admin_client):
    """overflow=True + propose_split 成功 → 409 + split_candidates"""
    with patch("app.api.knowledge.refine_knowledge",
               return_value=_refine_result("x" * 300, "", True)), \
         patch("app.api.knowledge.propose_split",
               return_value=[
                   _refine_result("规则1", "A"),
                   _refine_result("规则2", "B"),
               ]), \
         patch("app.api.knowledge.detect_conflicts",
               return_value=_conflict_report([])):
        r = await admin_client.post("/api/knowledge", json={
            "entry_type": "rule",
            "content": "一大段...",
            "namespace_id": None,
            "tier": "critical",
        })

    assert r.status_code == 409
    body = r.json()
    assert body["overflow"] is True
    assert len(body["split_candidates"]) == 2
    assert body["split_candidates"][0]["refined"] == "规则1"


@pytest.mark.asyncio
async def test_create_knowledge_overflow_empty_split_when_llm_fails(db, admin_client):
    """overflow=True + propose_split 返回 [] → 409 overflow=true split=[] 前端显示提示"""
    with patch("app.api.knowledge.refine_knowledge",
               return_value=_refine_result("x" * 300, "", True)), \
         patch("app.api.knowledge.propose_split", return_value=[]):
        r = await admin_client.post("/api/knowledge", json={
            "entry_type": "rule",
            "content": "一大段...",
            "namespace_id": None,
            "tier": "critical",
        })

    assert r.status_code == 409
    body = r.json()
    assert body["overflow"] is True
    assert body["split_candidates"] == []


@pytest.mark.asyncio
async def test_create_knowledge_filters_oversized_split_candidates(db, admin_client):
    """propose_split 返回自身仍然 overflow 的候选应被过滤"""
    with patch("app.api.knowledge.refine_knowledge",
               return_value=_refine_result("x" * 300, "", True)), \
         patch("app.api.knowledge.propose_split", return_value=[
             _refine_result("y" * 300, "", True),  # 坏 — 仍超长
             _refine_result("短规则", "D", False),  # 好
         ]):
        r = await admin_client.post("/api/knowledge", json={
            "entry_type": "rule",
            "content": "...",
            "namespace_id": None,
            "tier": "critical",
        })

    assert r.status_code == 409
    body = r.json()
    assert len(body["split_candidates"]) == 1
    assert body["split_candidates"][0]["refined"] == "短规则"


@pytest.mark.asyncio
async def test_create_knowledge_404_when_namespace_missing(admin_client):
    """namespace_id 不存在 → 404"""
    r = await admin_client.post("/api/knowledge", json={
        "entry_type": "rule",
        "content": "x",
        "namespace_id": 999,
        "tier": "normal",
    })
    assert r.status_code == 404
