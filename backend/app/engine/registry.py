"""
引擎注册中心 — ChromaDB 单例 + 知识向量集合管理

ChromaDB 单例: get_chroma_client() 是进程内唯一的 PersistentClient 创建点
所有消费者(知识检索/清理)借用此实例, 禁止自行创建
"""

import threading

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings
from app.logging_config import get_logger

log = get_logger("registry")

# ════════════════════════════════════════════
#  ChromaDB 进程级单例 — 双重检查锁
# ════════════════════════════════════════════

_chroma_client: chromadb.ClientAPI | None = None  # type: ignore[reportPrivateImportUsage]
_chroma_lock = threading.Lock()


def get_chroma_client() -> chromadb.ClientAPI:  # type: ignore[reportPrivateImportUsage]
    """进程级 ChromaDB 单例 — 唯一的 PersistentClient 创建点"""
    global _chroma_client
    if _chroma_client is None:
        with _chroma_lock:
            if _chroma_client is None:
                _chroma_client = chromadb.PersistentClient(
                    path=settings.chroma_persist_dir,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                log.info("ChromaDB 单例创建 path=%s", settings.chroma_persist_dir)
    return _chroma_client


def delete_knowledge_collection(slug: str) -> None:
    """删除 ns_{slug}_knowledge ChromaDB 集合 — 命名空间删除时清理向量数据，不存在则静默跳过"""
    client = get_chroma_client()
    try:
        client.delete_collection(name=f"ns_{slug}_knowledge")
        log.info("[registry] 知识集合已删除 slug=%s", slug)
    except Exception as exc:
        log.warning("[registry] delete_knowledge_collection(%s) failed: %s", slug, exc)


# ════════════════════════════════════════════
#  知识库向量集合
# ════════════════════════════════════════════

def get_knowledge_collection(slug: str) -> "chromadb.Collection":
    """
    返回命名空间专属知识向量集合 ns_{slug}_knowledge

    幂等: get_or_create — 首次调用自动建集合, 后续直接返回.
    消费方: knowledge_retriever (录入/删除/召回)

    embedding_function 显式注入 — 避免 ChromaDB 默认 all-MiniLM-L6-v2 对中文无能.
    """
    from app.engine.embedding import get_embedding_function

    client = get_chroma_client()
    coll_name = f"ns_{slug}_knowledge"
    coll = client.get_or_create_collection(
        name=coll_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=get_embedding_function(),  # type: ignore[arg-type]
    )
    log.debug("知识集合就绪 collection=%s", coll_name)
    return coll
