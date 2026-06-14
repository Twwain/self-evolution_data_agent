"""migration_022: DataSource 加 description + db_profile_json 字段."""
import json

import pytest
from sqlalchemy import select

from app.models import DataSource, Namespace


@pytest.mark.asyncio
async def test_datasource_has_description_and_profile_defaults(db):
    """新字段有默认值: description='' / db_profile_json='{}'."""
    ns = Namespace(name="t_ds_profile", slug="t-ds-profile")
    db.add(ns)
    await db.flush()
    ds = DataSource(
        namespace_id=ns.id, db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p",
    )
    db.add(ds)
    await db.flush()
    await db.refresh(ds)
    assert ds.description == ""
    assert ds.db_profile_json == "{}"
    assert json.loads(ds.db_profile_json) == {}


@pytest.mark.asyncio
async def test_datasource_profile_roundtrip(db):
    """db_profile_json 可存取 JSON 字符串."""
    ns = Namespace(name="t_ds_rt", slug="t-ds-rt")
    db.add(ns)
    await db.flush()
    profile = {"version": "8.0.32", "charset": "utf8mb4", "object_count": 87}
    ds = DataSource(
        namespace_id=ns.id, db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p",
        description="电力运维库", db_profile_json=json.dumps(profile),
    )
    db.add(ds)
    await db.flush()
    await db.refresh(ds)
    assert ds.description == "电力运维库"
    assert json.loads(ds.db_profile_json) == profile
