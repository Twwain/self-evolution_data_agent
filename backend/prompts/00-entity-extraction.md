# Prompt: 00-entity-extraction

## Name
entity_extraction

## Input shape
- java_files: 一批 Java 源文件 (含 seed entity + [参考上下文] DTO/枚举/嵌入类型)
- hidden_entity_collections: dict[class_name, collection_name] (DAO 反查得到的隐藏 entity)

## Expected output shape (JSON)
```json
{
  "entities": [
    {
      "class_name": "str",
      "table": "str",
      "columns": [
        {
          "name": "str",
          "type": "str",
          "column": "str",
          "enum_class_hint": "str | null"
        }
      ],
      "relations": [],
      "file": "str"
    }
  ],
  "mongo_docs": [
    {
      "class_name": "str",
      "collection": "str",
      "description": "str | null",
      "purpose_detail": "str | null",
      "fields": [
        {
          "field": "str",
          "type": "str",
          "description": "str | null",
          "enum_class_hint": "str | null",
          "sub_fields": []
        }
      ],
      "file": "str"
    }
  ]
}
```

## Validation rules
- entities / mongo_docs 各自非数组时拒收
- enum_class_hint 写 simple name (无包路径)
- 输出非合法 JSON → ExtractionFailureLog(failure_type=llm_parse_error)

## Failure handling
- LLM HTTP 4xx/5xx → with_retry 4 次指数退避; 全失败入 ExtractionFailureLog
- 输出非 JSON → 一次性入 ExtractionFailureLog (不重试)

## 模板正文

prompt 主体内联在 `backend/app/knowledge/code_parser.py:_JAVA_SYSTEM_PROMPT`, 不通过 prompt_loader 加载.
本文件仅作为契约文档, 实际修改请直接编辑 code_parser.py.
