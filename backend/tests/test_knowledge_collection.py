"""
Task 3.1 — ChromaDB ns_{slug}_knowledge collection 注册测试
验证 get_knowledge_collection 的幂等性与命名约定
"""

from unittest.mock import MagicMock, patch

import pytest


# ════════════════════════════════════════════
#  测试用例
# ════════════════════════════════════════════

def test_get_knowledge_collection_returns_correct_name():
    """get_knowledge_collection("myns") → collection name = ns_myns_knowledge"""
    mock_coll = MagicMock()
    mock_coll.name = "ns_myns_knowledge"
    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_coll

    with patch("app.engine.registry.get_chroma_client", return_value=mock_client), \
         patch("app.engine.embedding.get_embedding_function", return_value=None):
        from app.engine.registry import get_knowledge_collection
        coll = get_knowledge_collection("myns")

    # Stage 1 Task 6: get_or_create_collection 现在带 embedding_function kwarg
    mock_client.get_or_create_collection.assert_called_once()
    kwargs = mock_client.get_or_create_collection.call_args.kwargs
    assert kwargs["name"] == "ns_myns_knowledge"
    assert kwargs["metadata"] == {"hnsw:space": "cosine"}
    assert "embedding_function" in kwargs
    assert coll.name == "ns_myns_knowledge"


def test_get_knowledge_collection_idempotent():
    """多次调用返回相同 collection (幂等 get_or_create)"""
    mock_coll = MagicMock()
    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_coll

    with patch("app.engine.registry.get_chroma_client", return_value=mock_client):
        from app.engine.registry import get_knowledge_collection
        c1 = get_knowledge_collection("demo")
        c2 = get_knowledge_collection("demo")

    # get_or_create_collection 被调用两次 (无本地缓存, 每次都走 chroma)
    assert mock_client.get_or_create_collection.call_count == 2
    assert c1 is c2  # mock 返回同一对象


def test_get_knowledge_collection_slug_isolation():
    """不同 slug 产生不同 collection name — 命名空间隔离"""
    calls: list[str] = []

    def fake_get_or_create(name, metadata, **_kwargs):
        calls.append(name)
        m = MagicMock()
        m.name = name
        return m

    mock_client = MagicMock()
    mock_client.get_or_create_collection.side_effect = fake_get_or_create

    with patch("app.engine.registry.get_chroma_client", return_value=mock_client), \
         patch("app.engine.embedding.get_embedding_function", return_value=None):
        from app.engine.registry import get_knowledge_collection
        get_knowledge_collection("ns_a")
        get_knowledge_collection("ns_b")

    assert "ns_ns_a_knowledge" in calls
    assert "ns_ns_b_knowledge" in calls
    assert calls[0] != calls[1]
