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


async def shutdown_all_drivers():
    """uvicorn shutdown hook 调用, 优雅关闭所有 driver 连接池."""
    for driver in DRIVERS.values():
        if hasattr(driver, "close_all"):
            await driver.close_all()  # type: ignore[attr-defined]
