"""
Schema 构建器 — 将解析结果转为可训练文档
解析器产出结构化数据, 这里转为 DDL + 中文描述 + 示例 SQL
"""


def build_ddl_from_jpa(entities: list[dict]) -> list[str]:
    """JPA Entity → DDL 语句.

    entity dict 契约 (来自 _map_agent_to_channels):
      {"table": str, "table_name": str, "fields": [{"name": str, "field": str, "type": str}, ...]}
    """
    ddls = []
    for e in entities:
        fields = e.get("fields") or []
        if not fields:
            continue  # 无字段的实体跳过, 不生成空 DDL
        cols = ", ".join(
            f"{f['name']} {_java_to_sql_type(f.get('type', 'String'))}" for f in fields
        )
        tbl = e.get("table") or e.get("table_name") or "unknown"
        ddls.append(f"CREATE TABLE {tbl} ({cols});")
    return ddls


def build_doc_from_jpa(entities: list[dict]) -> list[str]:
    """JPA Entity → 中文文档描述.

    entity dict 契约 (来自 _map_agent_to_channels):
      {"table": str, "table_name": str, "fields": [...], "relations": [...]}
    relations 挂在 entity dict 上与 fields 并列 — 共享同一 database gate, 不剥离独立通道.
    """
    docs = []
    for e in entities:
        fields = e.get("fields") or []
        if not fields:
            continue
        col_desc = ", ".join(
            f"{f['name']}({f.get('type', 'String')})" for f in fields
        )
        tbl = e.get("table") or e.get("table_name") or "unknown"
        cls_name = e.get("table_name") or e.get("table") or tbl
        doc = f"表 {tbl} (来自 {cls_name}): 包含字段 {col_desc}"
        docs.append(doc)
    return docs


def build_example_sql_from_mybatis(mybatis_entries: list[dict]) -> list[str]:
    """MyBatis SQL → 示例 SQL (仅 select)"""
    examples = []
    for entry in mybatis_entries:
        if entry["type"] != "select":
            continue
        sql = entry["sql"]
        # 清理 MyBatis 动态标签残留
        sql = sql.replace("#{", ":").replace("}", "").replace("${", ":").strip()
        if sql:
            examples.append(sql)
    return examples


def _java_to_sql_type(java_type: str) -> str:
    """Java 类型 → SQL 类型 (近似映射, 用于文档而非实际建表)"""
    mapping = {
        "Long": "BIGINT", "long": "BIGINT",
        "Integer": "INT", "int": "INT",
        "String": "VARCHAR(255)",
        "Boolean": "TINYINT(1)", "boolean": "TINYINT(1)",
        "Double": "DOUBLE", "double": "DOUBLE",
        "Float": "FLOAT", "float": "FLOAT",
        "BigDecimal": "DECIMAL(19,4)",
        "Date": "DATETIME", "LocalDateTime": "DATETIME", "LocalDate": "DATE",
        "Instant": "TIMESTAMP",
    }
    return mapping.get(java_type, "VARCHAR(255)")
