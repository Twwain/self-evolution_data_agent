# backend/tests/knowledge/test_code_parser_prompt.py
import re

from app.knowledge import code_parser


def test_java_system_prompt_includes_enum_class_hint_schema():
    """prompt 必须在 mongo_docs.fields 和 entities.columns 两处 schema 中提到 enum_class_hint."""
    text = code_parser._JAVA_SYSTEM_PROMPT
    assert "enum_class_hint" in text
    # mongo_docs.fields 示例段含 enum_class_hint
    assert re.search(
        r'"field":\s*"\w+".*?"enum_class_hint"',
        text,
        re.DOTALL,
    ), "mongo_docs.fields 示例缺 enum_class_hint"
    # 约束块标识
    assert "枚举类关联推断 enum_class_hint" in text
