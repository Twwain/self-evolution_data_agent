"""加载 prompts/ 目录下的 markdown 模板.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/06-prompts.md §6.9
所有运行时 prompt md 统一收口到 backend/prompts/ (扁平目录, 文件名唯一).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

# backend/app/knowledge/prompt_loader.py → parents[2] = backend/
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@dataclass(slots=True)
class PromptTemplate:
    name: str
    body: str

    def render(self, **kwargs: Any) -> str:
        return Template(self.body).safe_substitute(**kwargs)


def _find_prompt_file(name: str) -> Path:
    fp = _PROMPTS_DIR / f"{name}.md"
    if fp.exists():
        return fp
    raise FileNotFoundError(f"Prompt {name!r} not found in {_PROMPTS_DIR}")


def load_prompt(name: str) -> PromptTemplate:
    """读 <prompts_dir>/<name>.md, 提取 '## 模板正文' 后的 ``` 块作为 body."""
    fp = _find_prompt_file(name)
    text = fp.read_text(encoding="utf-8")
    marker = "## 模板正文"
    idx = text.find(marker)
    if idx < 0:
        raise ValueError(f"prompt {name} missing '## 模板正文' section")
    body_section = text[idx + len(marker):]
    fence_start = body_section.find("```")
    fence_end = body_section.rfind("```")
    if fence_start < 0 or fence_end <= fence_start:
        raise ValueError(f"prompt {name} 模板正文 fence 缺失")
    body_start = body_section.find("\n", fence_start) + 1
    body = body_section[body_start:fence_end].strip()
    return PromptTemplate(name=name, body=body)
