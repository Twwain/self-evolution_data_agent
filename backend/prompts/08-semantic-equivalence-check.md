# Prompt: 08-semantic-equivalence-check

## Name
semantic_equivalence_check

## Input shape
- question_a: 问题 A
- question_b: 问题 B
- schema_context: 相关 schema 摘要

## Expected output shape (JSON)
```json
{
  "equivalent": true,
  "confidence": 0.95,
  "reason": "str"
}
```

## Validation rules
- equivalent 为 boolean
- confidence 在 0.0-1.0 之间
- reason 非空

## Failure handling
- LLM HTTP 4xx/5xx → with_retry 4 次指数退避
- 输出非 JSON → ExtractionFailureLog(failure_type=llm_parse_error)

## 模板正文
```text
Role: 语义等价判断器
Goal: 判断两个自然语言问题是否在数据库查询语义上等价

Constraints:
- 等价 = 两个问题对应的 SQL 查询结果集完全相同
- 仅表述不同 (同义词/语序) 视为等价
- 约束不同 (多/少 WHERE 条件) 视为不等价
- confidence 反映判断确信度

Output format (strict JSON):
{
  "equivalent": <true|false>,
  "confidence": <0.0-1.0>,
  "reason": "<判断依据>"
}

<input>
question_a: $question_a
question_b: $question_b
schema_context: $schema_context
</input>
```
