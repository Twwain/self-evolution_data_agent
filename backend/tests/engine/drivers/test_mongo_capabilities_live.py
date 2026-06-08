"""L1 live test — connects to ds=3 (the same MongoDB that exposed
trace 173dff87's $round failure). Skipped without IS_METADATA_DB_URL."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.engine.drivers.mongo import MongoDriver
from app.models import DataSource

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_real_mongo_buildinfo_and_unsupported_ops():
    url = os.environ.get("IS_METADATA_DB_URL")
    if not url:
        pytest.skip("IS_METADATA_DB_URL not set")
    engine = create_async_engine(url)
    SM = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SM() as s:
            ds = (await s.execute(
                select(DataSource).where(DataSource.id == 3)
            )).scalar_one_or_none()
        if ds is None:
            pytest.skip("ds=3 not found in metadata db")

        driver = MongoDriver()
        caps = await driver.get_server_capabilities(ds)
        assert caps is not None, "buildInfo must return non-None on real ds=3"
        assert caps["version"], "version field must be non-empty"
        print(f"\n[live] ds=3 version={caps['version']}")
        print(f"[live] agg_ops_unsupported={caps['agg_ops_unsupported']}")
    finally:
        await engine.dispose()
