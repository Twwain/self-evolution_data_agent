#!/usr/bin/env python3
"""
SQLite → PostgreSQL 数据迁移脚本

用法:
    cd backend && python -m scripts.migrate_sqlite_to_pg
    cd backend && python -m scripts.migrate_sqlite_to_pg --sqlite-url sqlite:///./data/metadata.db

PG 目标地址从 IS_METADATA_DB_URL 环境变量读取 (通过 app.config.settings).
"""
import argparse
import asyncio
import sys
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.types import DateTime, Date

import app.models  # noqa: F401 — 确保所有 ORM model 被导入, 否则 Base.metadata.sorted_tables 为空
from app.config import settings
from app.models.base import Base

BATCH_SIZE = 1000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate metadata from SQLite to PostgreSQL")
    parser.add_argument(
        "--sqlite-url",
        default="sqlite:///./data/metadata.db",
        help="SQLite source URL (default: sqlite:///./data/metadata.db)",
    )
    return parser.parse_args()


def _read_all_rows(sqlite_url: str, table_name: str) -> list[dict]:
    """用同步 sqlite3 引擎读取整张表的所有行, 返回 list[dict]."""
    engine = create_engine(sqlite_url)
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {table_name}"))  # noqa: S608
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    engine.dispose()
    return rows


def _coerce_datetime_columns(table, rows: list[dict]) -> list[dict]:
    """将 SQLite 中以字符串存储的 datetime 值转换为 Python datetime 对象.

    asyncpg 要求 TIMESTAMP 列传入 datetime 实例, 不接受字符串.
    """
    # 找出表中所有 DateTime/Date 类型的列
    dt_columns = set()
    for col in table.columns:
        if isinstance(col.type, (DateTime, Date)):
            dt_columns.add(col.name)

    if not dt_columns:
        return rows

    _FORMATS = [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]

    for row in rows:
        for col_name in dt_columns:
            val = row.get(col_name)
            if val is None or isinstance(val, datetime):
                continue
            if not isinstance(val, str):
                continue
            for fmt in _FORMATS:
                try:
                    row[col_name] = datetime.strptime(val, fmt)
                    break
                except ValueError:
                    continue

    return rows


async def _batch_insert_to_pg(conn, table, rows: list[dict]) -> int:
    """分批插入数据到 PostgreSQL (在已有连接/事务内), 每批最多 BATCH_SIZE 行. 返回插入总行数."""
    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        await conn.execute(table.insert(), batch)
        inserted += len(batch)
    return inserted


async def _reset_sequence(pg_engine, table) -> None:
    """重置 PostgreSQL SERIAL 序列到当前最大 ID + 1."""
    pk_col = table.primary_key.columns.values()[0].name
    async with pg_engine.begin() as conn:
        result = await conn.execute(text(f"SELECT MAX({pk_col}) FROM {table.name}"))  # noqa: S608
        max_id = result.scalar() or 0
        if max_id > 0:
            seq_name = f"{table.name}_{pk_col}_seq"
            await conn.execute(
                text("SELECT setval(:seq, :val, true)"),
                {"seq": seq_name, "val": max_id},
            )


async def _get_pg_row_count(pg_engine, table_name: str) -> int:
    """查询 PostgreSQL 中指定表的行数."""
    async with pg_engine.connect() as conn:
        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))  # noqa: S608
        return result.scalar() or 0


def _print_summary(results: list[dict]) -> None:
    """打印迁移结果汇总表到 stdout."""
    print()
    print("═" * 61)
    print(" Migration Summary")
    print("═" * 61)
    print(f" {'Table':<24} | {'Rows':<7} | Status")
    print("─" * 61)

    for r in results:
        table_name = r["table"]
        row_count = r["rows"]
        status = r["status"]

        if row_count is None:
            rows_str = "---"
        else:
            rows_str = str(row_count)

        if status == "success":
            status_str = "✅ success"
        elif status == "skipped":
            status_str = "⏭️ skipped (0 rows)"
        elif status == "mismatch":
            expected = r.get("expected", "?")
            status_str = f"⚠️ mismatch (expected {expected})"
        elif status == "read_failed":
            status_str = "❌ read failed"
        elif status == "failed":
            status_str = "❌ failed"
        else:
            status_str = status

        print(f" {table_name:<24} | {rows_str:>7} | {status_str}")

    print("═" * 61)


async def migrate_sqlite_to_postgresql(sqlite_url: str, pg_url: str) -> None:
    """完整数据迁移: SQLite → PostgreSQL."""
    print(f"源: {sqlite_url}")
    print(f"目标: {pg_url}")
    print()

    # Step 1: Create PG async engine
    pg_engine = create_async_engine(pg_url)

    # Step 2: Create all tables in PostgreSQL
    print("创建 PostgreSQL 表结构...")
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("表结构创建完成")
    print()

    # Step 3: Get table dependency order (topological sort by FK)
    ordered_tables = Base.metadata.sorted_tables

    # Step 4: Migrate data table by table
    print(f"开始迁移数据 ({len(ordered_tables)} 张表)...")
    print("-" * 60)

    results: list[dict] = []

    for table in ordered_tables:
        table_name = table.name

        # Read from SQLite
        try:
            rows = _read_all_rows(sqlite_url, table_name)
        except Exception as e:
            print(f"  {table_name}: 读取失败 — {e}", file=sys.stderr)
            results.append({"table": table_name, "rows": None, "status": "read_failed"})
            continue

        sqlite_count = len(rows)

        if not rows:
            print(f"  {table_name}: 0 行 (跳过)")
            results.append({"table": table_name, "rows": 0, "status": "skipped"})
            continue

        # Insert into PG within a single transaction per table
        # TRUNCATE first for idempotent re-execution (no-op on empty table)
        try:
            async with pg_engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE {table_name} CASCADE"))  # noqa: S608
                rows = _coerce_datetime_columns(table, rows)
                inserted = await _batch_insert_to_pg(conn, table, rows)

            # Verify row count in PG after commit
            pg_count = await _get_pg_row_count(pg_engine, table_name)

            if pg_count != sqlite_count:
                print(
                    f"  {table_name}: ⚠️ 行数不匹配 — "
                    f"SQLite={sqlite_count}, PG={pg_count}",
                    file=sys.stderr,
                )
                results.append({
                    "table": table_name,
                    "rows": pg_count,
                    "status": "mismatch",
                    "expected": sqlite_count,
                })
            else:
                print(f"  {table_name}: {inserted} 行已迁移 ✓")
                results.append({"table": table_name, "rows": inserted, "status": "success"})

        except Exception as e:
            # Transaction is automatically rolled back by pg_engine.begin() context manager
            print(f"  {table_name}: 插入失败 — {e}", file=sys.stderr)
            results.append({"table": table_name, "rows": None, "status": "failed"})

    print("-" * 60)
    print("数据迁移完成")
    print()

    # Step 5: Reset sequences for SERIAL primary keys
    print("重置序列...")
    for table in ordered_tables:
        if not table.primary_key.columns:
            continue
        pk_col = table.primary_key.columns.values()[0]
        if not pk_col.autoincrement:
            continue
        try:
            await _reset_sequence(pg_engine, table)
            print(f"  {table.name}: 序列已重置")
        except Exception as e:
            print(f"  {table.name}: 序列重置失败 — {e}", file=sys.stderr)

    print()

    # Print summary
    _print_summary(results)

    await pg_engine.dispose()


def main():
    args = _parse_args()
    sqlite_url = args.sqlite_url
    pg_url = settings.metadata_db_url

    asyncio.run(migrate_sqlite_to_postgresql(sqlite_url, pg_url))


if __name__ == "__main__":
    main()
