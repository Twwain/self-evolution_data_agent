"""数据源驱动层 — 多态 dispatch.

详见 docs/superpowers/specs/2026-05-09-agent-loop-multi-driver/04-driver-abstraction.md
"""
from __future__ import annotations

from app.engine.drivers._exceptions import UnsupportedDataSourceTypeError
from app.engine.drivers.base import DataSourceDriver
from app.engine.drivers.mongo import MongoDriver
from app.engine.drivers.mysql import MySQLDriver

DRIVERS: dict[str, DataSourceDriver] = {
    "mysql": MySQLDriver(),
    "mongodb": MongoDriver(),
}


def get_driver(db_type: str) -> DataSourceDriver:
    """按 db_type 获取 driver 单例. 未注册则抛 UnsupportedDataSourceType."""
    if db_type not in DRIVERS:
        raise UnsupportedDataSourceTypeError(
            f"未支持的 db_type: {db_type}",
            suggestion=f"已注册: {sorted(DRIVERS.keys())}",
        )
    return DRIVERS[db_type]


async def evict_datasource(ds_id: int) -> None:
    """DataSource 删除/变更时清理各 driver 中按 ds_id 缓存的连接池/客户端.

    一个 ds 只属于一种 db_type, 对另一种 driver 的清理是 no-op pop (幂等安全).
    防止 CASCADE 删 datasources 行后, driver 内 _pools/_clients/_caps_cache 残留
    持有 TCP 连接直到进程重启.
    """
    mysql = DRIVERS.get("mysql")
    if mysql is not None and hasattr(mysql, "invalidate_pool"):
        await mysql.invalidate_pool(ds_id)  # type: ignore[attr-defined]
    mongo = DRIVERS.get("mongodb")
    if mongo is not None and hasattr(mongo, "invalidate_client"):
        await mongo.invalidate_client(ds_id)  # type: ignore[attr-defined]


async def shutdown_all_drivers():
    """uvicorn shutdown hook 调用, 优雅关闭所有 driver 连接池."""
    for driver in DRIVERS.values():
        if hasattr(driver, "close_all"):
            await driver.close_all()  # type: ignore[attr-defined]
