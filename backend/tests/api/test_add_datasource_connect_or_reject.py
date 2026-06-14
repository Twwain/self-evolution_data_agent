"""add_datasource 改'连通才存': 连不上 400 不落库; 连通则存 + 画像."""
import pytest
from sqlalchemy import select

from app.models import DataSource, Namespace


@pytest.mark.asyncio
async def test_add_datasource_unreachable_rejects_and_no_row(make_client, db):
    """连不上的 host → 400, 且 datasources 表无新行."""
    ns = Namespace(name="t_addds_rej", slug="t-addds-rej")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    resp = await client.post(
        f"/api/namespaces/{ns.id}/datasources",
        json={
            "db_type": "mysql",
            "host": "unreachable.invalid",  # 不可达主机 (RFC 6761 .invalid, DNS 必失败)
            "port": 3306, "database": "nope",
            "username": "u", "password": "p",
            "description": "应当被拒绝",
        },
    )
    assert resp.status_code == 400, resp.text

    rows = (await db.execute(
        select(DataSource).where(DataSource.namespace_id == ns.id)
    )).scalars().all()
    assert len(rows) == 0  # 不落库
