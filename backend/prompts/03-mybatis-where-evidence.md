# Prompt: 03-mybatis-where-evidence

## Name
mybatis_where_evidence

## Input shape
- mapper_xml: MyBatis mapper XML 片段
- schema_context: 相关表/集合 schema 摘要

## Expected output shape (JSON)
```json
{
  "where_clauses": [
    {
      "table": "str",
      "column": "str",
      "operator": "str",
      "frequency": 0.0,
      "sample_methods": ["str"]
    }
  ]
}
```

## Validation rules
- where_clauses 为数组
- table 和 column 非空
- operator 为 SQL 操作符

## Failure handling
- LLM HTTP 4xx/5xx → with_retry 4 次指数退避
- 输出非 JSON → ExtractionFailureLog(failure_type=llm_parse_error)

## 模板正文
```text
Role: MyBatis SQL WHERE 条件分析器
Goal: 从 MyBatis mapper XML 中提取 WHERE 条件模式, 统计高频过滤字段

Constraints:
- 只提取 WHERE 子句中的条件, 不含 JOIN ON 条件
- 动态 SQL (<if>, <choose>) 中的条件也要提取
- frequency 为该条件在所有 SELECT 方法中出现的比例 (0.0-1.0)
- 如果无法确定 table, 从 FROM 子句推断

Output format (strict JSON):
{
  "where_clauses": [
    {
      "table": "<表名>",
      "column": "<字段名>",
      "operator": "<= | != | IN | LIKE | BETWEEN | > | < | IS NULL>",
      "frequency": <0.0-1.0>,
      "sample_methods": ["<method_id_1>", "<method_id_2>"]
    }
  ]
}

<mapper_xml>
$mapper_xml
</mapper_xml>

<schema_context>
$schema_context
</schema_context>
```
