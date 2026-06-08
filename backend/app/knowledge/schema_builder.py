"""
Schema 构建器 — 将解析结果转为可训练文档
解析器产出结构化数据, 这里转为 DDL + 中文描述 + 示例 SQL
"""


def build_ddl_from_jpa(entities: list[dict]) -> list[str]:
    """JPA Entity → DDL 语句"""
    ddls = []
    for e in entities:
        cols = ", ".join(
            f"{c['column']} {_java_to_sql_type(c['type'])}" for c in e["columns"]
        )
        ddl = f"CREATE TABLE {e['table']} ({cols});"
        ddls.append(ddl)
    return ddls


def build_doc_from_jpa(entities: list[dict]) -> list[str]:
    """JPA Entity → 中文文档描述"""
    docs = []
    for e in entities:
        col_desc = ", ".join(f"{c['column']}({c['type']})" for c in e["columns"])
        doc = f"表 {e['table']} (来自 {e['class_name']}): 包含字段 {col_desc}"
        if e.get("relations"):
            rels = ", ".join(f"{r['field']}→{r['target']}({r['type']})" for r in e["relations"])
            doc += f"; 关联关系: {rels}"
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
