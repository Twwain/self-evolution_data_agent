"""Stage 2 — present_result prompt 文件化 + 加载 + 产品化红线."""
from __future__ import annotations


def test_present_result_prompt_loads():
    from app.knowledge.prompt_loader import load_prompt
    t = load_prompt("present_result")
    assert t.body
    assert "ref" in t.body and "result_ref" in t.body
    # 产品化红线: 示例只用通用领域词 (order/region 等); 客户领域词由 publish 闸门兜底,
    # 测试不内联敏感词 (内联会让守卫词本身泄漏到公开镜像).
    assert "order" in t.body or "region" in t.body
