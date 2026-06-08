"""Stage 2 抓手 E — agent_traces 列表 / 详情 / 批量提炼 API."""

from __future__ import annotations

import asyncio
import json as _json
import logging
from datetime import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.config import settings
from app.db.metadata import get_db
from app.models import AgentTrace
from app.models.user import User

router = APIRouter(tags=["agent-traces"])
log = logging.getLogger(__name__)


@router.get("/api/agent-traces")
async def list_traces(
    namespace_id: int | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列表 agent_traces, 支持 namespace_id / status 过滤, 分页."""
    stmt = select(AgentTrace).order_by(AgentTrace.created_at.desc())
    if namespace_id is not None:
        stmt = stmt.where(AgentTrace.namespace_id == namespace_id)
    if status:
        stmt = stmt.where(AgentTrace.status == status)
    stmt = stmt.offset((page - 1) * size).limit(size)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        # tool_call_count: 解析 trace_json 取 tool_trace 列表长度
        tcc = 0
        if r.trace_json:
            try:
                trace_data = _json.loads(r.trace_json)
                if isinstance(trace_data, list):
                    tcc = len(trace_data)
                elif isinstance(trace_data, dict):
                    tt = trace_data.get("tool_trace")
                    tcc = len(tt) if isinstance(tt, list) else 0
            except (_json.JSONDecodeError, TypeError):
                tcc = 0
        out.append({
            "id": r.id,
            "trace_id": r.trace_id,
            "namespace_id": r.namespace_id,
            "user_query": r.user_query,
            "status": r.status,
            "refined_at": r.refined_at.isoformat() if r.refined_at else None,
            "created_at": r.created_at.isoformat(),
            "tool_call_count": tcc,
        })
    return out


@router.get("/api/agent-traces/{trace_id}")
async def get_trace_detail(
    trace_id: str,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取单条 trace 详情 (含完整 trace_json + reflection_log_json)."""
    row = (await db.execute(
        select(AgentTrace).where(AgentTrace.trace_id == trace_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "trace 不存在")
    return {
        "id": row.id,
        "trace_id": row.trace_id,
        "namespace_id": row.namespace_id,
        "user_query": row.user_query,
        "trace_json": row.trace_json,
        "reflection_log_json": row.reflection_log_json,
        "status": row.status,
        "refined_at": row.refined_at.isoformat() if row.refined_at else None,
        "refined_summary": row.refined_summary,
        "created_at": row.created_at.isoformat(),
    }


# ════════════════════════════════════════════
#  POST /api/agent-traces/refine — 批量提炼
# ════════════════════════════════════════════


class RefineRequest(BaseModel):
    trace_ids: list[str]


class RefineOut(BaseModel):
    proposed_count: int
    proposed_ke_ids: list[int]


@router.post("/api/agent-traces/refine", response_model=RefineOut)
async def refine_traces_endpoint(
    body: RefineRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量提炼 agent traces → 产 proposed KE 入待审池."""
    if len(body.trace_ids) > settings.agent_trace_refine_batch_max:
        raise HTTPException(
            422, f"batch 上限 {settings.agent_trace_refine_batch_max}",
        )
    rows = (await db.execute(
        select(AgentTrace).where(
            AgentTrace.trace_id.in_(body.trace_ids),
            AgentTrace.status == "completed",
        )
    )).scalars().all()
    if not rows:
        return RefineOut(proposed_count=0, proposed_ke_ids=[])

    # ── 解析 namespace ─ refine 走 save_knowledge 需要 (ns_id, ns_slug). ──
    #    所有 trace 同 namespace_id (前端只在单 ns 视角发起批量提炼).      ──
    from app.models.namespace import Namespace
    ns_id = rows[0].namespace_id
    ns_slug: str | None = None
    if ns_id is not None:
        ns = (await db.execute(
            select(Namespace).where(Namespace.id == ns_id)
        )).scalar_one_or_none()
        ns_slug = ns.slug if ns else None

    from app.knowledge.trace_refiner import refine_traces

    # ── 拉 critical rules 注入 trace_refiner 已知禁区, 防 LLM 重复总结 ──
    from sqlalchemy import select as _select
    from app.models.knowledge_entry import KnowledgeEntry
    critical_rules: list[str] = []
    if ns_id is not None:
        critical_stmt = (
            _select(KnowledgeEntry.content)
            .where(KnowledgeEntry.namespace_id == ns_id)
            .where(KnowledgeEntry.tier == "critical")
            .where(KnowledgeEntry.status == "canonical")
        )
        critical_rules = [
            r[0] for r in (await db.execute(critical_stmt)).all() if r[0]
        ]

    payload = [
        {
            "trace_id": r.trace_id,
            "user_query": r.user_query,
            "trace_json": r.trace_json,
            "reflection_log_json": r.reflection_log_json,
        }
        for r in rows
    ]
    proposed = await asyncio.to_thread(refine_traces, payload, critical_rules)

    # ── Phase 2: allowlist 过滤 LLM payload + trace_extractor 补机械字段 ──
    from app.knowledge.trace_extractor import (
        derive_cost_strategy,
        extract_collections,
        extract_db_context,
        extract_final_pipeline,
        extract_join_fields,
        extract_primary_query_json,
    )

    # LLM 语义字段 allowlist — 多塞字段静默丢弃
    llm_allowed_fields: dict[str, set[str]] = {
        "terminology": {"term", "primary_collection", "synonyms",
                        "primary_field", "source_collections"},
        "instance_alias": {"alias", "canonical_name", "target_id", "id_field"},
        "route_hint": {"question_pattern", "reason", "avoid_path"},
        "rule": {"rule_text", "rule_kind", "applies_to_collections",
                 "priority", "evidence"},
        "example": {"question", "result_summary", "nl_paraphrases",
                    "schema_hash", "extraction_source"},
    }

    trace_by_id: dict[str, "AgentTrace"] = {r.trace_id: r for r in rows}

    for p in proposed:
        # ── allowlist 过滤 LLM payload ──
        allowed = llm_allowed_fields.get(p.entry_type, set())
        if allowed:
            p.payload = {k: v for k, v in (p.payload or {}).items() if k in allowed}

        # ── code 补机械字段 ──
        src = trace_by_id.get(p.source_trace_id)
        if src is None and rows:
            # fallback: LLM 没返 source_trace_id 时用第一条 trace
            src = rows[0]
        if src is None:
            continue

        try:
            tool_trace = (
                _json.loads(src.trace_json or "{}") or {}
            ).get("tool_trace") or []
            if isinstance(tool_trace, list) is False:
                tool_trace = []
        except (_json.JSONDecodeError, TypeError):
            tool_trace = []

        collections = extract_collections(tool_trace)
        db_type, database = extract_db_context(tool_trace)

        if p.entry_type == "terminology":
            if db_type:
                p.payload["db_type"] = db_type
            if database:
                p.payload["primary_database"] = database
            # primary_collection LLM 已产语义判断, 缺则用 trace 第一个集合兜底
            if not p.payload.get("primary_collection") and collections:
                p.payload["primary_collection"] = collections[0]
        elif p.entry_type == "instance_alias":
            if database:
                p.payload["target_database"] = database
            if not p.payload.get("target_collection") and collections:
                p.payload["target_collection"] = collections[0]
        elif p.entry_type == "example":
            if not p.payload.get("target_collection") and collections:
                p.payload["target_collection"] = collections[0]
            if database:
                p.payload["target_database"] = database
            qj = extract_primary_query_json(tool_trace)
            if qj is not None:
                p.payload["query_json"] = qj
        elif p.entry_type == "route_hint":
            if collections:
                p.payload["collection_path"] = collections
            p.payload["cost_strategy"] = derive_cost_strategy(tool_trace)
            final_pipeline = extract_final_pipeline(tool_trace)
            joins = extract_join_fields(final_pipeline)
            if joins:
                p.payload["join_fields"] = joins
        elif p.entry_type == "rule":
            if collections:
                p.payload.setdefault("applies_to_collections", collections)

    # ── 收口到 save_knowledge 接口: 抓手 D 演化 + terminology 唯一键闸门 + ──
    #     instance_alias schema 校验 — 同一治理路径与 agent 自学等价 (spec     ──
    #     02-stage2-pull-reinforcement.md 写入治理表第 3 / 4 行同列要求).     ──
    from app.engine.tools.knowledge_tools import save_knowledge

    async def _noop_sse(_evt: dict) -> None:
        return None

    new_ids: list[int] = []
    ns_slug_for_save = ns_slug or ""
    for p in proposed:
        try:
            ret = await save_knowledge(
                db=db,
                namespace_id=ns_id,
                ns_slug=ns_slug_for_save,
                sse_emit=_noop_sse,
                entry_type=p.entry_type,
                content=p.content,
                payload=p.payload,
                evidence=p.evidence,
                tier="normal",
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[refine] save_knowledge 写入异常, 跳过该提案: type=%s reason=%s",
                p.entry_type, e,
            )
            await db.rollback()
            continue

        # save_knowledge 校验/冲突失败时不返回 entry_id, 跳过.
        if not isinstance(ret, dict) or "entry_id" not in ret:
            log.info(
                "[refine] proposal skipped by save_knowledge: type=%s reason=%r",
                p.entry_type, ret.get("reason") if isinstance(ret, dict) else ret,
            )
            continue
        new_ids.append(int(ret["entry_id"]))

    # 标 traces 为 refined
    for r in rows:
        r.status = "refined"
        r.refined_at = _dt.now()
        r.refined_summary = _json.dumps({
            "proposed_ke_ids": new_ids,
            "count": len(proposed),
        }, ensure_ascii=False)
    await db.commit()

    return RefineOut(proposed_count=len(proposed), proposed_ke_ids=new_ids)
