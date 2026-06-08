# Prompt: 01-enum-class-extraction

## Name
enum_class_extraction

## Input shape
- enum_source: 单个 Java enum 类源码 (≤ 500 行)
- file_path: 文件路径 (元数据回溯, 不入推理)

## Expected output shape (JSON)
```json
{
  "enum_class": "str",
  "fully_qualified_name": "str",
  "values": [
    {"name": "str", "db_value": "int | str", "description": "str | null"}
  ]
}
```

## Validation rules
- values 非空数组
- 每个 value name 非空
- db_value 类型一致 (全 int 或全 string)
- 输出非合法 JSON → ExtractionFailureLog(failure_type=llm_parse_error)

## Failure handling
- LLM HTTP 4xx/5xx → with_retry 4 次指数退避; 全失败入 ExtractionFailureLog
- 输出非 JSON → 一次性入 ExtractionFailureLog (不重试)

## 模板正文
```text
Role: Java enum 分析器
Goal: 从单个 Java enum 类源码中提取枚举常量名、db_value、description

Constraints:
- db_value 推断: 有构造参数 (如 CREATED(1, "已创建")) → 取首位 int 作 db_value
- 无构造参数 (纯名字枚举) → db_value = name (字符串)
- description 推断顺序: Javadoc → 构造参数末位 String → null
- fully_qualified_name 从 package 声明推断
- 如果无法确定某个值, 使用 null

Output format (strict JSON):
{
  "enum_class": "<类名>",
  "fully_qualified_name": "<包名.类名>",
  "values": [
    {"name": "<常量名>", "db_value": <int或string>, "description": "<描述或null>"}
  ]
}

INPUT (file: $file_path):
$enum_source

Rules:
- db_value: if constructor has int as 1st arg, take it; if 1st arg is String, take it; if no constructor, set db_value = name.
- description: prefer Javadoc above the value; fallback to last String arg in constructor; else null.
- Output JSON only, no prose.
```
