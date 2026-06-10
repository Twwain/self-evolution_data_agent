"""MySQL 异步驱动 — aiomysql 连接池 + SQL 安全执行."""
from __future__ import annotations

import asyncio
import logging
import re
import time

import aiomysql
import sqlparse

from app.config import settings
from app.engine.drivers._exceptions import (
    ConnectionFailureError,
    PayloadShapeMismatchError,
    QueryTimeoutError,
    UnsafeQueryError,
)
from app.engine.drivers.base import (
    CostEstimate,
    ExecuteMode,
    ExecuteResult,
    FieldDef,
    SchemaSnapshot,
    ServerCapabilities,
)
from app.models import DataSource

log = logging.getLogger(__name__)

_SAFE_FIELD_RE = re.compile(r"^[\w\u4e00-\u9fff]+$")
_DML_DDL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE|CALL)\b",
    re.IGNORECASE,
)


class MySQLDriver:
    """aiomysql 连接池驱动, 实现 DataSourceDriver 协议."""

    db_type: str = "mysql"

    def __init__(self) -> None:
        self._pools: dict[int, aiomysql.Pool] = {}

    async def _get_pool(self, ds: DataSource) -> aiomysql.Pool:
        """获取或创建 ds 对应的连接池."""
        if ds.id in self._pools:
            pool = self._pools[ds.id]
            if not pool.closed:
                return pool
        try:
            pool = await aiomysql.create_pool(
                host=ds.host,
                port=ds.port,
                user=ds.username,
                password=ds.password,
                db=ds.database,
                maxsize=settings.mysql_pool_max_size,
                pool_recycle=3600,  # noqa: hardcode
                connect_timeout=settings.mysql_pool_timeout_secs,
                autocommit=True,
                charset="utf8mb4",
            )
        except Exception as exc:
            raise ConnectionFailureError(
                f"MySQL 连接失败: {ds.host}:{ds.port}/{ds.database} — {exc}",
                suggestion="检查 host/port/credentials 是否正确",
            ) from exc
        self._pools[ds.id] = pool
        return pool

    # ── fetch_schema ─────────────────────────────────────

    async def fetch_schema(
        self,
        ds: DataSource,
        target: str | None = None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        log.info("[mysql_driver] fetch_schema ds=%d target=%s", ds.id, target)
        pool = await self._get_pool(ds)
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if target is None:
                    # 列出所有表
                    await cur.execute(
                        "SELECT TABLE_NAME, TABLE_ROWS, TABLE_COMMENT "
                        "FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'",
                        (ds.database,),
                    )
                    tables = await cur.fetchall()
                    results: list[SchemaSnapshot] = []
                    for t in tables:
                        results.append(
                            SchemaSnapshot(
                                db_type="mysql",
                                database=ds.database,
                                target=t["TABLE_NAME"],
                                description=t.get("TABLE_COMMENT") or "",
                                fields=[],
                                indexes=[],
                                sample_count=t.get("TABLE_ROWS") or 0,
                            )
                        )
                    return results

                # 单表详情
                await cur.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT, "
                    "IS_NULLABLE, COLUMN_KEY "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (ds.database, target),
                )
                columns = await cur.fetchall()

                fields: list[FieldDef] = []
                for col in columns:
                    fields.append(
                        FieldDef(
                            name=col["COLUMN_NAME"],
                            type=col["COLUMN_TYPE"],
                            description=col.get("COLUMN_COMMENT") or "",
                            indexed=col.get("COLUMN_KEY", "") != "",
                            nullable=col.get("IS_NULLABLE") == "YES",
                        )
                    )

                # 索引信息
                await cur.execute(
                    "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SEQ_IN_INDEX "
                    "FROM INFORMATION_SCHEMA.STATISTICS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                    (ds.database, target),
                )
                raw_indexes = await cur.fetchall()
                indexes: list[dict] = []
                idx_map: dict[str, dict] = {}
                for row in raw_indexes:
                    name = row["INDEX_NAME"]
                    if name not in idx_map:
                        idx_map[name] = {
                            "name": name,
                            "unique": row["NON_UNIQUE"] == 0,
                            "columns": [],
                        }
                    idx_map[name]["columns"].append(row["COLUMN_NAME"])
                indexes = list(idx_map.values())

                # 行数估算 + 表注释
                await cur.execute(
                    "SELECT TABLE_ROWS, TABLE_COMMENT FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                    (ds.database, target),
                )
                row = await cur.fetchone()
                sample_count = (row or {}).get("TABLE_ROWS") or 0
                table_comment = (row or {}).get("TABLE_COMMENT") or ""

                return SchemaSnapshot(
                    db_type="mysql",
                    database=ds.database,
                    target=target,
                    description=table_comment,
                    fields=fields,
                    indexes=indexes,
                    sample_count=sample_count,
                )

    # ── inspect_values ───────────────────────────────────

    async def inspect_values(
        self,
        ds: DataSource,
        target: str,
        field: str,
        limit: int = 10,
    ) -> list[dict]:
        log.info("[mysql_driver] inspect_values ds=%d target=%s field=%s", ds.id, target, field)
        if not _SAFE_FIELD_RE.match(field):
            raise UnsafeQueryError(
                f"字段名不合法: {field!r}",
                suggestion="字段名仅允许字母/数字/下划线/中文",
            )
        if not _SAFE_FIELD_RE.match(target):
            raise UnsafeQueryError(
                f"表名不合法: {target!r}",
                suggestion="表名仅允许字母/数字/下划线/中文",
            )
        pool = await self._get_pool(ds)
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                sql = (
                    f"SELECT `{field}`, COUNT(*) AS cnt "  # noqa: S608
                    f"FROM `{target}` "
                    f"GROUP BY `{field}` "
                    f"ORDER BY cnt DESC LIMIT %s"
                )
                await cur.execute(sql, (limit,))
                return await cur.fetchall()

    # ── estimate_cost ────────────────────────────────────

    async def estimate_cost(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> CostEstimate:
        log.info("[mysql_driver] estimate_cost ds=%d target=%s", ds.id, target)
        sql = query.get("sql", "")
        if not sql:
            raise PayloadShapeMismatchError(
                "estimate_cost 需要 query.sql",
                suggestion="payload 必须包含 'sql' key",
            )
        pool = await self._get_pool(ds)
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(f"EXPLAIN {sql}")  # noqa: S608
                rows = await cur.fetchall()
                estimated_rows = sum(int(r.get("rows") or 0) for r in rows)
                if estimated_rows > settings.query_cost_total_limit:
                    level = "blocked"
                elif estimated_rows > settings.query_cost_single_layer_limit:
                    level = "high"
                else:
                    level = "ok"
                return CostEstimate(
                    estimated_rows=estimated_rows,
                    warning_level=level,
                    raw_explain={"rows": rows},
                )

    # ── execute_query ────────────────────────────────────

    async def execute_query(
        self,
        ds: DataSource,
        target: str,
        query: dict,
        mode: ExecuteMode = "single",
        batch_size: int = 1000,  # noqa: hardcode
    ) -> ExecuteResult:
        log.info("[mysql_driver] execute_query ds=%d target=%s mode=%s", ds.id, target, mode)
        sql = query.get("sql")
        if not sql:
            raise PayloadShapeMismatchError(
                "execute_query 需要 query.sql",
                suggestion="payload 必须包含 'sql' key",
            )
        self._enforce_select_only(sql)
        sql = self._wrap_by_mode(sql, mode, batch_size)

        pool = await self._get_pool(ds)
        t0 = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await asyncio.wait_for(
                        cur.execute(sql),
                        timeout=settings.mysql_query_timeout_secs,
                    )
                    rows = await cur.fetchall()
        except asyncio.TimeoutError as exc:
            raise QueryTimeoutError(
                f"SQL 执行超时 ({settings.mysql_query_timeout_secs}s): {sql[:100]}",
                suggestion="优化查询或增加 IS_MYSQL_QUERY_TIMEOUT_SECS",
            ) from exc
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        truncated = False
        if mode == "single" and len(rows) >= settings.query_row_limit:
            truncated = True

        log.info(
            "[mysql_driver] execute_query done ds=%d rows=%d elapsed_ms=%d",
            ds.id,
            len(rows),
            elapsed_ms,
        )
        return ExecuteResult(
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    # ── health_check ─────────────────────────────────────

    async def health_check(self, ds: DataSource) -> bool:
        try:
            pool = await self._get_pool(ds)
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ── get_server_capabilities ──────────────────────────

    async def get_server_capabilities(
        self, ds: DataSource,
    ) -> ServerCapabilities | None:
        """MySQL has no equivalent agg-op version table; return None."""
        return None

    # ── lifecycle ────────────────────────────────────────

    async def invalidate_pool(self, ds_id: int) -> None:
        """关闭并移除指定 ds 的连接池."""
        pool = self._pools.pop(ds_id, None)
        if pool and not pool.closed:
            pool.close()
            await pool.wait_closed()

    async def close_all(self) -> None:
        """关闭所有连接池."""
        for ds_id in list(self._pools.keys()):
            await self.invalidate_pool(ds_id)

    # ── private helpers ──────────────────────────────────

    @staticmethod
    def _enforce_select_only(sql: str) -> None:
        """拒绝非 SELECT 语句, 拒绝 DML/DDL, 拒绝多语句."""
        parsed = sqlparse.parse(sql)
        if not parsed:
            raise UnsafeQueryError("空 SQL", suggestion="提供有效的 SELECT 语句")
        if len(parsed) > 1:
            raise UnsafeQueryError(
                "禁止多语句执行",
                suggestion="每次只允许一条 SQL",
            )
        stmt = parsed[0]  # type: ignore[index]
        stmt_type = stmt.get_type()
        if stmt_type and stmt_type.upper() != "SELECT":
            raise UnsafeQueryError(
                f"仅允许 SELECT, 检测到: {stmt_type}",
                suggestion="移除非查询语句",
            )
        if _DML_DDL_KEYWORDS.search(sql):
            raise UnsafeQueryError(
                "SQL 包含禁止的 DML/DDL 关键字",
                suggestion="仅允许 SELECT 查询",
            )

    @staticmethod
    def _wrap_by_mode(sql: str, mode: ExecuteMode, batch_size: int) -> str:
        """按 mode 包装 SQL (添加 LIMIT). 已有 LIMIT 时不重复添加."""
        # 去除尾部分号
        sql = sql.rstrip().rstrip(";")
        has_limit = "LIMIT" in sql.upper()
        if mode == "probe":
            return sql if has_limit else f"{sql} LIMIT 10"
        elif mode == "count":
            return f"SELECT COUNT(*) AS cnt FROM ({sql}) AS _sub"
        elif mode == "batched":
            return sql if has_limit else f"{sql} LIMIT {batch_size}"
        else:
            # single — 使用全局 row_limit (已有 LIMIT 时不覆盖)
            return sql if has_limit else f"{sql} LIMIT {settings.query_row_limit}"
