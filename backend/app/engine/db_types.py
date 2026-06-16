"""数据库类型常量 — 全局唯一定义, 避免字符串散落全栈.

使用方式:
    from app.engine.db_types import DB_TYPE_ORACLE, SQL_DB_TYPES
"""

DB_TYPE_MYSQL = "mysql"
DB_TYPE_MONGODB = "mongodb"
DB_TYPE_ORACLE = "oracle"

# SQL 型数据源: 共享 agent SQL 查询形态, 执行层走 _execute_sql_step
SQL_DB_TYPES: frozenset[str] = frozenset({DB_TYPE_MYSQL, DB_TYPE_ORACLE})

# 全部支持的数据库类型 (schema 校验、driver 注册白名单)
SUPPORTED_DB_TYPES: frozenset[str] = frozenset({DB_TYPE_MYSQL, DB_TYPE_MONGODB, DB_TYPE_ORACLE})
