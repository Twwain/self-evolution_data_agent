# Prompt: 05-terminology-extraction

## Name
terminology_extraction

## Input shape
- entity_context: Entity 类字段摘要
- mapper_context: Mapper SQL 摘要

## Expected output shape (JSON)
```json
{
  "terms": [
    {
      "term": "str",
      "synonyms": ["str"],
      "primary_collection": "str",
      "primary_database": "str | null",
      "db_type": "mysql|mongodb",
      "primary_field": "str | null",
      "source_collections": ["str"]
    }
  ]
}
```

## Validation rules
- terms 为数组
- term 为单一名词, 不超过 20 字
- synonyms 包含英文原名

## Failure handling
- LLM HTTP 4xx/5xx → with_retry 4 次指数退避
- 输出非 JSON → ExtractionFailureLog(failure_type=llm_parse_error)

## 模板正文
```text
Role: 业务术语抽取器
Goal: 从 Entity 类和 Mapper SQL 上下文中识别业务名词锚点

Constraints:
- term 为单一名词, 不超过 20 字, 无句号/分号
- term 是业务概念 (如 "订单"), 不是技术名词 (如 "String")
- synonyms 包含英文原名和常见别称
- 如果无法确定某个字段, 使用 null
- 多句业务陈述不属于 terminology, 应走 rule 抽取

Output format (strict JSON):
{
  "terms": [
    {
      "term": "<业务名词>",
      "synonyms": ["<同义词1>", "<同义词2>"],
      "primary_collection": "<主集合/表名>",
      "primary_database": null,
      "db_type": "<mysql|mongodb>",
      "primary_field": null,
      "source_collections": ["<相关集合1>", "<相关集合2>"]
    }
  ]
}

<entity_context>
$entity_context
</entity_context>

<mapper_context>
$mapper_context
</mapper_context>
```
