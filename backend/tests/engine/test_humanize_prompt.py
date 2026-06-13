"""Stage 4 — humanize 查询生成段文件化 + 注入单步/多步 system_prompt."""
from __future__ import annotations


def test_humanize_prompt_loads_and_clean():
    from app.knowledge.prompt_loader import load_prompt
    t = load_prompt("humanize_query_gen")
    assert "JOIN" in t.body or "CASE" in t.body
    # 通用领域示例 (dim_region/orders/status); 不内联客户领域词 (publish 闸门兜底,
    # 内联守卫词本身会泄漏到公开镜像).
    assert "region" in t.body or "orders" in t.body


def test_system_prompt_includes_humanize():
    from app.config import settings
    from app.engine.tools.registry import build_system_prompt
    prompt = build_system_prompt(settings=settings, namespace=None)
    # humanize 段已并入 (展示列输出 label 不输出 code)
    assert "label" in prompt and ("JOIN" in prompt or "可读" in prompt)


def test_planner_system_includes_humanize():
    from app.engine.plan_generator import _PLANNER_SYSTEM
    assert "label" in _PLANNER_SYSTEM and ("JOIN" in _PLANNER_SYSTEM or "可读" in _PLANNER_SYSTEM)
