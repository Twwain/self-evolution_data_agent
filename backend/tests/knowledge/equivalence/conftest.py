"""equivalence registry test isolation.

snapshot-restore _REGISTRY: 测试前 deep-copy, yield, 测试后清空+还原.
防止"清空后没重新注册"导致后续 promote 集成测试在空 registry 下落 conflict.
"""
from __future__ import annotations

import pytest

from app.knowledge.equivalence.registry import _REGISTRY


@pytest.fixture(autouse=True)
def _isolate_equivalence_registry():
    """snapshot-restore: 让 equivalence 单测可以自由 clear/register, 退出时还原生产 5 条."""
    snapshot = list(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.extend(snapshot)
