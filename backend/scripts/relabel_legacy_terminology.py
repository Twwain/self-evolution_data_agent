"""Phase 0 数据治理: LLM 逐条重打标 entry_type=terminology 错配 KE.

策略:
- 5 类宪章: terminology / instance_alias / rule / example / route_hint
- LLM 分类失败 → 默认 rule (R1 决策)
- 已处理跳过 (audit_log.action='relabel' AND entry_id 命中)
- ChromaDB 同步: 调 delete_knowledge_entry (旧向量删除, best-effort)

USAGE:
    cd backend
    python -m scripts.relabel_legacy_terminology
环境变量: IS_LEGACY_RELABEL_BATCH_SIZE (默认 10)
"""

import asyncio
import json
import logging
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.metadata import async_session
from app.engine.llm import chat_completion
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry

log = logging.getLogger(__name__)

# ── 5 类宪章合法值 (镜像 intake.VALID_ENTRY_TYPES) ──────────────────
VALID_TYPES = {"terminology", "instance_alias", "rule", "example", "route_hint"}

CLASSIFY_PROMPT = """\
你是知识分类专家. 给定一条历史错配的 KnowledgeEntry, 判断它应归到 5 类宪章中的哪一类:

- terminology: 单一业务名词及其同义词 (如 "商品", "订单"); 必须是名词, ≤20 字, 无句号
- rule: 业务约束 / 关联规则 (如 "x 默认 latestVersion=true", "c_product.categoryId 关联 c_category._id")
- example: query→sql/mql 示例对
- route_hint: 多 collection 决策路径
- instance_alias: 别名 → 具体记录映射

返回严格的 JSON: {{"entry_type": "<one of 5>", "reason": "<≤30 字>"}}
仅返 JSON, 不要其他文字.

输入:
  payload.term: {term}
  content: {content}
"""


# ════════════════════════════════════════════
#  LLM 分类入口 — 失败返 None 让调用方走 R1 兜底
# ════════════════════════════════════════════
async def _llm_classify(payload_term: str, content: str) -> str | None:
    """调 LLM 分类.

    chat_completion 是同步函数, 用 asyncio.to_thread 包装防阻塞事件循环.

    返回:
      - 成功: LLM 返回的 entry_type 字符串 (调用方校验是否在 VALID_TYPES 内)
      - JSON 解析失败 / 缺字段: None (走 R1 兜底 rule)
      - chat_completion 抛异常: 上抛 (调用方 catch 后走 R1 兜底 rule)
    """
    prompt = CLASSIFY_PROMPT.format(term=payload_term[:200], content=content[:500])
    try:
        text = await asyncio.to_thread(
            chat_completion,
            [{"role": "user", "content": prompt}],
        )
    except Exception:
        log.exception("[relabel] llm chat_completion failed")
        raise
    try:
        parsed = json.loads((text or "").strip())
        return parsed.get("entry_type")
    except (json.JSONDecodeError, AttributeError, KeyError):
        return None


# ════════════════════════════════════════════
#  幂等保护 — 已写过 'relabel' audit 的 KE 跳过
# ════════════════════════════════════════════
async def _is_already_processed(db: AsyncSession, entry_id: int) -> bool:
    row = await db.execute(
        select(KnowledgeAuditLog.id).where(
            KnowledgeAuditLog.entry_id == entry_id,
            KnowledgeAuditLog.action == "relabel",
        ).limit(1)
    )
    return row.scalar_one_or_none() is not None


# ════════════════════════════════════════════
#  ChromaDB 旧向量删除 — best-effort 不阻业务
# ════════════════════════════════════════════
async def _delete_chromadb_vectors(
    ns_slug: str, entry_id: int, namespace_id: int | None = None
) -> None:
    """从 ns_{slug}_knowledge 集合删除旧向量.

    必须传 ``namespace_id``, 否则 ``delete_knowledge_entry`` 会回落到 ``__global__``
    集合 (其内部 ``target_slug = slug if namespace_id is not None else GLOBAL_NS_SLUG``),
    导致真集合的孤儿向量永远删不掉.
    """
    try:
        from app.knowledge.knowledge_retriever import delete_knowledge_entry

        await asyncio.to_thread(
            delete_knowledge_entry, ns_slug, entry_id, namespace_id
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[relabel] chromadb delete failed entry_id=%d: %s", entry_id, e)


async def _resolve_ns_slug(db: AsyncSession, ns_id: int | None) -> str | None:
    if ns_id is None:
        return None
    from app.models.namespace import Namespace

    ns = await db.get(Namespace, ns_id)
    return ns.slug if ns else None


# ════════════════════════════════════════════
#  主循环 — 单批次处理, 单事务提交
# ════════════════════════════════════════════
async def relabel_one_batch(db: AsyncSession, entry_ids: list[int]) -> None:
    """处理一批 entry. SQLite 单事务一致性优先, ChromaDB 同步在 commit 后 best-effort.

    每条 KE 必写一条 audit_log (action='relabel'), 即使类型不变也写,
    保证幂等检测下次能识别已处理. 仅 entry_type 真变更的 KE 进入 ChromaDB
    清向量队列, 防止 commit 失败导致 SQLite 回滚但 ChromaDB 已删的不一致.
    """
    # (ns_slug, entry_id, namespace_id) for type-changed entries — namespace_id
    # 必须带到 _delete_chromadb_vectors, 否则 None 会让 delete 回落 __global__ 集合
    chromadb_to_delete: list[tuple[str, int, int | None]] = []

    for eid in entry_ids:
        if await _is_already_processed(db, eid):
            log.debug("[relabel] skip already-processed entry_id=%d", eid)
            continue
        ke = await db.get(KnowledgeEntry, eid)
        if ke is None or ke.entry_type != "terminology":
            continue

        try:
            payload = json.loads(ke.payload or "{}")
        except json.JSONDecodeError:
            payload = {}
        old_type = ke.entry_type
        new_type: str
        reason: str

        try:
            llm_type = await _llm_classify(payload.get("term", ""), ke.content or "")
            if llm_type is None:
                new_type = "rule"
                reason = "llm_failed_fallback:json_parse_error"
            elif llm_type not in VALID_TYPES:
                new_type = "rule"
                reason = f"invalid_entry_type:{llm_type}"
            else:
                new_type = llm_type
                reason = f"llm_classified:{llm_type}"
        except Exception as e:  # noqa: BLE001
            new_type = "rule"
            reason = f"llm_failed_fallback:{type(e).__name__}"
            log.exception("[relabel] llm error entry_id=%d", eid)

        # 仅在类型真变更时改 KE; audit_log 无论变不变都写, 用于幂等锚定
        if old_type != new_type:
            ke.entry_type = new_type

        log_row = KnowledgeAuditLog(
            entry_id=ke.id,
            actor_id=None,
            action="relabel",
            from_status=ke.status,
            to_status=ke.status,
            reason=reason,
            diff_json=json.dumps({
                "before": {"entry_type": old_type},
                "after": {"entry_type": new_type},
            }),
        )
        db.add(log_row)
        await db.flush()

        # 收集类型变更的 (ns_slug, entry_id, namespace_id), 待 commit 成功后再清向量.
        # 必须带 namespace_id 让 delete 命中真集合 (None 会回落 __global__, 详见
        # _delete_chromadb_vectors docstring).
        if old_type != new_type:
            ns_slug = await _resolve_ns_slug(db, ke.namespace_id)
            if ns_slug:
                chromadb_to_delete.append((ns_slug, ke.id, ke.namespace_id))

    await db.commit()

    # commit 成功后才动 ChromaDB (避免 SQLite rollback 后向量已删的不一致)
    for ns_slug, eid, ns_id in chromadb_to_delete:
        await _delete_chromadb_vectors(ns_slug, eid, ns_id)


# ════════════════════════════════════════════
#  Phase 1 闸门预检 — terminology_conflicts 表 / 索引就位才允许 relabel
# ════════════════════════════════════════════
async def _is_phase1_gate_ready(db: AsyncSession) -> bool:
    """Phase 1 闸门: ``terminology_conflicts`` 表必须已建.

    Phase 1a Task 1.1 用 unique constraint 加在 KE 上, 表存在 + migration_008+
    已运行即视为 Phase 1 已就位 (索引由 schema_migrations 强制保证).
    返 False 时 ``main`` 拒绝执行, 防止 Phase 0 数据治理踩在未升级的 schema 上.
    """
    row = await db.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = 'terminology_conflicts'"
        )
    )
    return row.scalar_one_or_none() is not None


# ════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════
async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    async with async_session() as db:
        if not await _is_phase1_gate_ready(db):
            raise RuntimeError(
                "Phase 1 gate not ready: terminology_conflicts table missing. "
                "Run schema migrations first (migration_008 or later) before relabel."
            )
    batch = settings.legacy_relabel_batch_size
    async with async_session() as db:
        all_ids = [
            r[0]
            for r in (
                await db.execute(
                    select(KnowledgeEntry.id).where(
                        KnowledgeEntry.entry_type == "terminology",
                        KnowledgeEntry.is_superseded == False,  # noqa: E712
                    )
                )
            ).all()
        ]
    log.info("[relabel] total %d KE candidates, batch=%d", len(all_ids), batch)
    for i in range(0, len(all_ids), batch):
        async with async_session() as db:
            await relabel_one_batch(db, all_ids[i : i + batch])
        log.info("[relabel] processed %d/%d", min(i + batch, len(all_ids)), len(all_ids))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
