"""Stage 4 Task 4 — inspect_field_values tool tests.

用 patch + AsyncMock 模拟 AsyncIOMotorClient (Motor 连真 MongoDB 在 CI 代价高).
真实数据路径已由生产调用覆盖.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import FakeMongoCursor


@pytest.mark.asyncio
async def test_inspect_field_returns_samples():
    from app.engine.tools.probe_tools import inspect_field_values

    fake_docs = [{"name": f"n{i}"} for i in range(10)]
    fake_coll = MagicMock()
    fake_coll.find = MagicMock(return_value=FakeMongoCursor(fake_docs))
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_coll

    with patch("app.engine.tools.probe_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await inspect_field_values(
            namespace_id=1, database="t", collection="c_product",
            field="name", sample=5,
        )
        assert out["collection"] == "c_product"
        assert out["field"] == "name"
        assert len(out["values"]) == 5
        assert out["values"][0] == "n0"
        assert out["truncated"] is True
        assert out["sample_requested"] == 5


@pytest.mark.asyncio
async def test_inspect_field_uses_default_sample(monkeypatch):
    from app.engine.tools import probe_tools
    monkeypatch.setattr(probe_tools.settings, "inspect_field_default_sample", 3)

    fake_docs = [{"x": i} for i in range(10)]
    fake_coll = MagicMock()
    fake_coll.find = MagicMock(return_value=FakeMongoCursor(fake_docs))
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_coll

    with patch("app.engine.tools.probe_tools.get_mongo_db",
                new=AsyncMock(return_value=fake_db)):
        out = await probe_tools.inspect_field_values(
            namespace_id=1, database="t", collection="c", field="x",
        )
        assert len(out["values"]) == 3
