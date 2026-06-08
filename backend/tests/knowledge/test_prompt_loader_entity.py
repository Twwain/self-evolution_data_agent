# backend/tests/knowledge/test_prompt_loader_entity.py
from pathlib import Path


def test_entity_extraction_prompt_file_exists():
    path = Path("../prompts/00-entity-extraction.md")
    assert path.exists() or Path("prompts/00-entity-extraction.md").exists()


def test_entity_extraction_prompt_has_enum_class_hint_section():
    candidates = [
        Path("../prompts/00-entity-extraction.md"),
        Path("prompts/00-entity-extraction.md"),
    ]
    for p in candidates:
        if p.exists():
            content = p.read_text(encoding="utf-8")
            assert "enum_class_hint" in content
            return
    raise AssertionError("prompt 文件未找到")
