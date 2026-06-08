"""Stage 4 Mongo tool helpers (shared across probe/prequery/cost/etc tools).

Extracted in Task 5 follow-up to avoid copy-paste explosion as Tasks 6-9 add
more Mongo-touching tools.
"""
from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import select

from app.db.metadata import async_session
from app.models import DataSource
from app.tracing import get_client as _lf_client

log = logging.getLogger(__name__)

# Label 候选字段优先级 (与 prequery.py:_LABEL_FIELDS 对齐)
_LABEL_FIELDS = ("name", "title", "label", "displayName", "cnName", "code")


def record_span_io(
    *,
    input: dict | None = None,
    output: dict | None = None,
    name: str | None = None,
) -> None:
    """Langfuse span IO 摘要, 无 trace 时静默 no-op.

    name (可选): 当前 span 显示名, 给 telemetry 区分多 record_span_io 同帧调用.
    """
    lf = _lf_client()
    if lf is None:
        return
    try:
        kwargs: dict = {"input": input, "output": output}
        if name is not None:
            kwargs["name"] = name
        lf.update_current_span(**kwargs)
    except Exception:
        pass


def build_mongo_uri(ds) -> str:
    """MongoDB 连接 URI 单点构造 (authSource=admin)."""
    return (
        f"mongodb://{ds.username}:{ds.password}"
        f"@{ds.host}:{ds.port}/{ds.database}?authSource=admin"
    )


async def get_mongo_db(
    *, namespace_id: int, database: str,
):
    """按 (namespace_id, database) 反查 datasource → 拿 Motor db handle.

    设计原则: datasource 是基础设施, 工具语义只关心 (namespace, database, collection).
    跨 mongo 集群部署时, 同 namespace 不同 database 可能落在不同 host/credentials,
    必须按 database 反查正确的 ds, 不能绑死单一 ds_id.

    Args:
        namespace_id: 命名空间 ID (dispatcher 注入)
        database: mongo database 名 (LLM 显式传)

    Raises:
        ValueError: 找不到匹配的 datasource

    调用方负责 close_db().
    """
    async with async_session() as sess:
        ds = (await sess.execute(
            select(DataSource).where(
                DataSource.namespace_id == namespace_id,
                DataSource.db_type == "mongodb",
                DataSource.database == database,
            ).limit(1)
        )).scalar_one_or_none()
    if ds is None:
        raise ValueError(
            f"datasource not found: ns={namespace_id} database={database!r}"
        )
    client = AsyncIOMotorClient(build_mongo_uri(ds))
    try:
        return client[database]
    except Exception:
        client.close()
        raise


def close_db(db_) -> None:
    """关闭 motor client (best-effort, 异常吞掉)."""
    client = getattr(db_, "client", None)
    if client and hasattr(client, "close"):
        try:
            client.close()
        except Exception:
            pass


def extract_candidate(
    doc: dict,
    value_field: str = "_id",
    extra_keep_fields: tuple[str, ...] = (),
) -> dict:
    """从 mongo 文档抽 {value, label, meta}.

    extra_keep_fields: 额外保留到 meta 的 label-class 字段名 (修 Task 5 review I2:
    若上层用 extract_field=name 串接, 必须 keep_fields=("name",) 否则 meta 丢字段).
    """
    value = str(doc.get(value_field, ""))
    label = ""
    for f in _LABEL_FIELDS:
        v = doc.get(f)
        if v is not None:
            label = str(v)
            break
    if not label:
        label = value
    excluded = set((value_field, *_LABEL_FIELDS)) - set(extra_keep_fields)
    meta = {
        k: str(v) for k, v in doc.items()
        if k not in excluded and v is not None
    }
    return {"value": value, "label": label, "meta": meta}
