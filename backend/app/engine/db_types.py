"""数据库类型常量 — 全局唯一定义, 避免字符串散落全栈.

使用方式:
    from app.engine.db_types import SQL_DB_TYPES
"""

# SQL 型数据源: 共享 agent SQL 查询形态, 执行层走 _execute_sql_step
SQL_DB_TYPES: frozenset[str] = frozenset({"mysql", "oracle"})
