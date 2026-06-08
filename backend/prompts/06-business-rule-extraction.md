# Prompt: 06-business-rule-extraction

## Name
business_rule_extraction

## Input shape
- where_statistics: WHERE 条件频次统计
- schema_context: 相关 schema 摘要

## Expected output shape (JSON)
```json
{
  "rules": [
    {
      "rule_text": "str",
      "applies_to_collections": ["str"],
      "rule_kind": "business_constraint|filter_default|join_pattern",
      "evidence": {"frequency": 0.0, "sample_mappers": ["str"]}
    }
  ]
}
```

## Validation rules
- rules 为数组
- rule_kind 三选一
- evidence.frequency 在 0.0-1.0 之间

## Failure handling
- LLM HTTP 4xx/5xx → with_retry 4 次指数退避
- 输出非 JSON → ExtractionFailureLog(failure_type=llm_parse_error)

## 模板正文
```text
Role: 业务规则分析器
Goal: 从高频 WHERE 条件统计中推断查询业务规则

Constraints:
- rule_kind 三选一: business_constraint / filter_default / join_pattern
- rule_text 可含多句, 描述规则含义
- frequency 来自预统计数据 (Python 计算), LLM 只解释其业务含义
- 如果证据不足以推断规则, 跳过该条件

Output format (strict JSON):
{
  "rules": [
    {
      "rule_text": "<规则描述>",
      "applies_to_collections": ["<表/集合名>"],
      "rule_kind": "<business_constraint|filter_default|join_pattern>",
      "evidence": {
        "frequency": <0.0-1.0>,
        "sample_mappers": ["<Mapper.method>"]
      }
    }
  ]
}

<where_statistics>
$where_statistics
</where_statistics>

<schema_context>
$schema_context
</schema_context>
```
