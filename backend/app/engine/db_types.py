"""数据库类型常量 — 从 DRIVERS 注册表派生, 单一真相源.

新增 driver 只需注册 DRIVERS + 填 paradigm, 本模块全部常量自动跟随.
"""
from __future__ import annotations

from app.engine.drivers import DRIVERS

# db_type → paradigm 映射 (如 "mysql" → "relational")
PARADIGM_MAP: dict[str, str] = {t: d.paradigm for t, d in DRIVERS.items()}

# SQL 型数据源 — 共享 agent SQL 查询形态, 执行层走 _execute_sql_step
SQL_DB_TYPES: frozenset[str] = frozenset(
    t for t, d in DRIVERS.items() if d.paradigm == "relational"
)

# Document 型数据源 — 走 _execute_mongo_step
DOCUMENT_DB_TYPES: frozenset[str] = frozenset(
    t for t, d in DRIVERS.items() if d.paradigm == "document"
)

# 全部已注册的 db_type — API schema regex + 前端类型同步
SUPPORTED_DB_TYPES: frozenset[str] = frozenset(DRIVERS.keys())

# 全部合法 paradigm 值 — agent emit 校验用
VALID_PARADIGMS: frozenset[str] = frozenset(
    {d.paradigm for d in DRIVERS.values()}
)
