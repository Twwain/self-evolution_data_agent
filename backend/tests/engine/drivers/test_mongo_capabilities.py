"""Unit tests for mongo_capabilities pure functions."""
from __future__ import annotations

import pytest

from app.engine.drivers.mongo_capabilities import (
    compute_unsupported_ops,
    parse_version,
)


class TestParseVersion:
    def test_three_part(self):
        assert parse_version("4.2.3") == (4, 2, 3)

    def test_two_part(self):
        assert parse_version("4.0") == (4, 0, 0)

    def test_with_patch_suffix(self):
        assert parse_version("4.0.28-rc0") == (4, 0, 28)

    def test_unknown(self):
        assert parse_version("unknown") == (0, 0, 0)

    def test_empty(self):
        assert parse_version("") == (0, 0, 0)


class TestComputeUnsupportedOps:
    def test_v4_0_blocks_round_dateTrunc_function(self):
        out = compute_unsupported_ops("4.0.28")
        assert "$round" in out
        assert "$dateTrunc" in out
        assert "$function" in out

    def test_v4_2_allows_round_blocks_dateTrunc(self):
        out = compute_unsupported_ops("4.2.0")
        assert "$round" not in out
        assert "$dateTrunc" in out
        assert "$function" in out  # 4.4 才有

    def test_v5_0_allows_dateTrunc_blocks_median(self):
        out = compute_unsupported_ops("5.0.0")
        assert "$round" not in out
        assert "$dateTrunc" not in out
        assert "$median" in out  # 7.0 才有
        assert "$percentile" in out

    def test_v7_0_allows_all(self):
        out = compute_unsupported_ops("7.0.0")
        assert "$round" not in out
        assert "$dateTrunc" not in out
        assert "$median" not in out
        assert "$percentile" not in out

    def test_unknown_version_returns_empty(self):
        # 未知 version 不能 false-positive 屏蔽算子
        assert compute_unsupported_ops("unknown") == []

    def test_returned_list_is_sorted(self):
        out = compute_unsupported_ops("4.0.0")
        assert out == sorted(out)


# ──────────────────────────────────────────────────────────
#  MongoDriver.get_server_capabilities (Task 3)
# ──────────────────────────────────────────────────────────

from unittest.mock import AsyncMock, MagicMock

from app.engine.drivers.mongo import MongoDriver
from app.models import DataSource


def make_ds(ds_id: int = 1) -> DataSource:
    ds = MagicMock(spec=DataSource)
    ds.id = ds_id
    ds.host = "h"
    ds.port = 27017
    ds.username = "u"
    ds.password = "p"
    ds.database = "db"
    return ds


class TestMongoDriverServerCapabilities:
    @pytest.mark.asyncio
    async def test_returns_version_and_unsupported_for_4_0(self):
        driver = MongoDriver()
        # 注入 mock client (绕过真连)
        client = MagicMock()
        client.admin.command = AsyncMock(return_value={"version": "4.0.28"})
        driver._clients[1] = client
        ds = make_ds(1)

        caps = await driver.get_server_capabilities(ds)
        assert caps is not None
        assert caps["version"] == "4.0.28"
        assert "$round" in caps["agg_ops_unsupported"]
        assert "$dateTrunc" in caps["agg_ops_unsupported"]

    @pytest.mark.asyncio
    async def test_caches_buildinfo_per_ds(self):
        driver = MongoDriver()
        client = MagicMock()
        client.admin.command = AsyncMock(return_value={"version": "5.0.0"})
        driver._clients[2] = client
        ds = make_ds(2)

        await driver.get_server_capabilities(ds)
        await driver.get_server_capabilities(ds)
        # buildInfo 仅调 1 次
        assert client.admin.command.call_count == 1

    @pytest.mark.asyncio
    async def test_buildinfo_failure_returns_none(self):
        driver = MongoDriver()
        client = MagicMock()
        client.admin.command = AsyncMock(side_effect=RuntimeError("boom"))
        driver._clients[3] = client
        ds = make_ds(3)

        caps = await driver.get_server_capabilities(ds)
        assert caps is None  # 不阻塞主链路, 上游照常工作
