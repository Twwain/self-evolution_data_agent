"""resolve_db_binding — db_type 产品 + database 的唯一真相源。

反查索引 `(paradigm, name) → list[(db_type, database)]` 由训练时连接每个 namespace
DataSource、列其库表/集合构建 (泛化现有 coll_to_db, 升为 paradigm 感知防跨范式同名误绑)。

三分支绑定:
  零命中 → 丢弃 (噪音: 该范式下该对象不在任何已接入的库)
  ≥1 命中 → 每个命中各产一条 bound (多绑不裁决)

extractor 自始至终只认 paradigm, 不碰 db_type 产品。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from app.knowledge.extractors.base import SchemaObject

log = logging.getLogger(__name__)

# ── db_type → paradigm 单一真相源 (内联自旧 engine.py:27-31, 切断 engine 依赖) ──
# 开放集: 新增 db_type 时扩这里 + 下游处理, 不破坏既有映射.
PARADIGM_MAP: dict[str, str] = {
    "mysql": "relational", "mariadb": "relational", "postgresql": "relational",
    "mssql": "relational", "oracle": "relational", "sqlite": "relational",
    "mongodb": "document", "cosmosdb": "document",
}


@dataclass
class BindingTarget:
    db_type: str
    database: str


@dataclass
class BindingResult:
    bound: list[tuple[SchemaObject, BindingTarget]] = field(default_factory=list)
    unbound: list[SchemaObject] = field(default_factory=list)


def _list_datasource_objects(ds) -> list[str]:
    """连接单个 DataSource, 列其库表/集合名 (复用 trainer coll_to_db 的连接逻辑)。

    测试通过 monkeypatch 此函数避免真实连接。连接失败抛异常, 由调用方隔离。
    """
    from app.config import settings

    if ds.db_type == "mongodb":
        from pymongo import MongoClient
        client = MongoClient(
            host=ds.host, port=ds.port,
            username=ds.username, password=ds.password,
            authSource="admin",
            serverSelectionTimeoutMS=settings.datasource_connect_timeout_ms,
        )
        try:
            return sorted(client[ds.database].list_collection_names())
        finally:
            client.close()
    elif ds.db_type in PARADIGM_MAP:  # 关系型家族走 SHOW TABLES (mysql 协议)
        # ⚠️ 既有债务 (从旧分支迁入, 非本 spec 引入): 所有 relational db_type 都用
        #    pymysql + SHOW TABLES, 对 Oracle/PostgreSQL/MSSQL 不适用。当前 binding 的
        #    resolve/build_reverse_index 未被 trainer 主路径调用 (trainer 用自己的
        #    _build_coll_to_db, 已按 db_type 正确分支), 故此债务暂不影响生产; 待 binding
        #    resolve 路径接通时再按 db_type 适配驱动。
        import pymysql
        conn = pymysql.connect(
            host=ds.host, port=ds.port, database=ds.database,
            user=ds.username, password=ds.password,
            connect_timeout=settings.datasource_connect_timeout_ms // 1000,  # noqa: hardcode
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW TABLES")
                return sorted(row[0] for row in cur.fetchall())
        finally:
            conn.close()
    return []


async def build_reverse_index(datasources) -> dict[tuple[str, str], list[BindingTarget]]:
    """连每个 DataSource 列库表/集合, 建 (paradigm, name) → [(db_type, database)] 反查索引。

    每个 DataSource 自带 paradigm (由 db_type 推导) + db_type, 故索引键含 paradigm,
    顺手白拿 db_type。连接失败 → 跳过该 DS + log.warning (best-effort, 不阻训练)。
    """
    index: dict[tuple[str, str], list[BindingTarget]] = defaultdict(list)
    reachable = 0
    for ds in datasources:
        paradigm = PARADIGM_MAP.get(ds.db_type)
        if paradigm is None:
            log.warning("binding: unknown db_type %r on datasource %s, skipped",
                        ds.db_type, getattr(ds, "id", "?"))
            continue
        try:
            names = await asyncio.to_thread(_list_datasource_objects, ds)
        except Exception as e:  # noqa: BLE001 — 单 DS 连接失败隔离
            log.warning("binding: datasource %s (%s) unreachable, skipped: %s",
                        getattr(ds, "id", "?"), ds.db_type, e)
            continue
        reachable += 1
        target = BindingTarget(db_type=ds.db_type, database=ds.database)
        for n in names:
            index[(paradigm, n)].append(target)
    if datasources and reachable == 0:
        log.warning("binding: all datasources unreachable, reverse index is empty")
    return dict(index)


def resolve(index: dict[tuple[str, str], list[BindingTarget]],
            objects: list[SchemaObject]) -> BindingResult:
    """三分支绑定: 零命中丢弃 / ≥1 命中全绑 (多绑不裁决)。"""
    result = BindingResult()
    if not index and objects:
        log.warning("binding: all datasources unreachable, %d objects unbound",
                    len(objects))
    for obj in objects:
        hits = index.get((obj.paradigm, obj.name), [])
        if not hits:
            result.unbound.append(obj)
            continue
        for target in hits:
            result.bound.append((obj, target))
    return result
