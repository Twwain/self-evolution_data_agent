"""Phase 1b Task 1.4 — terminology canonical 多向量入库.

设计契约:
- 一条 KE = 1 + N 个 ChromaDB doc (term + 每 synonym 各占一个 vec)
- 共享 entry_id metadata → retrieve_layer3 按 entry_id 去重保证 LLM 端"一条 KE"
- doc_id = ke_{entry_id}_{index} (term=0, synonym 起 1)
- role metadata 字段标识每个向量的语义角色 (term / synonym)
- 状态退离 canonical → 由 delete_terminology_vectors 全删 (where entry_id=...)

为何不复用单向量 ke_{id}: 同义词的 embedding 与 term 本身向量空间分离,
单向量只能选一个代表词, 同义词查询必丢召回. N+1 多向量让每个表述都有
独立 embedding, 跨语言/口语化 query 命中率显著提升.
"""

from __future__ import annotations

import logging

from app.knowledge.knowledge_retriever import GLOBAL_NS_SLUG
from app.schemas.knowledge_payload import TerminologyPayload

log = logging.getLogger(__name__)


def _multi_doc_id(entry_id: int, index: int) -> str:
    """ke_{entry_id}_{index} — index=0 是 term, 起 1 都是 synonym."""
    return f"ke_{entry_id}_{index}"


def upsert_terminology_vectors(
    *,
    slug: str,
    entry_id: int,
    payload: TerminologyPayload,
    namespace_id: int | None,
) -> None:
    """canonical terminology N+1 向量入库.

    metadata role 字段:
        - "term"    — index 0, payload.term 本体
        - "synonym" — index 1..N, payload.synonyms[i-1]
    """
    from app.engine.registry import get_knowledge_collection

    target_slug = slug if namespace_id is not None else GLOBAL_NS_SLUG
    coll = get_knowledge_collection(target_slug)

    # ── 构造 (text, role) 序列: term 在前, synonyms 在后 ─────
    docs: list[tuple[str, str]] = [(payload.term, "term")]
    docs.extend((s, "synonym") for s in payload.synonyms)

    ids = [_multi_doc_id(entry_id, i) for i in range(len(docs))]
    documents = [text for text, _ in docs]
    metadatas: list[dict[str, str | int | float | bool | None]] = [
        {
            "entry_id": entry_id,
            "namespace_id": namespace_id if namespace_id is not None else -1,
            "tier": "normal",
            "entry_type": "terminology",
            "status": "canonical",
            "role": role,
        }
        for _, role in docs
    ]
    coll.upsert(ids=ids, documents=documents, metadatas=metadatas)  # type: ignore[arg-type]
    log.debug(
        "[multivector] upsert ke=%d slug=%s n_docs=%d",
        entry_id, target_slug, len(docs),
    )


def delete_terminology_vectors(
    *,
    slug: str,
    entry_id: int,
    namespace_id: int | None,
) -> None:
    """删除该 KE 的全部 ke_{id}_* 多向量, 集合不存在静默通过.

    使用 where={"entry_id": entry_id} 扫净, 防止依赖 doc_id 个数变化的脆弱性
    (synonyms 数量变更 / 历史脏向量 / 跨版本兼容).
    """
    from app.engine.embedding import get_embedding_function
    from app.engine.registry import get_chroma_client

    target_slug = slug if namespace_id is not None else GLOBAL_NS_SLUG
    coll_name = f"ns_{target_slug}_knowledge"
    client = get_chroma_client()

    try:
        coll = client.get_collection(
            coll_name,
            embedding_function=get_embedding_function(),  # type: ignore[arg-type]
        )
    except Exception as exc:
        log.warning(
            "[multivector] delete skip — collection missing coll=%s err=%s",
            coll_name, exc,
        )
        return

    coll.delete(where={"entry_id": entry_id})
    log.debug("[multivector] delete ke=%d coll=%s", entry_id, coll_name)
