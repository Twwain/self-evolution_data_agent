"""Agentic 部分幻觉防御: 单一真实实体 → agent 不应凭空编造其他实体/字段 (live LLM)."""
import re

import pytest

from app.knowledge.extraction_agent import run_extraction_agent

pytestmark = pytest.mark.asyncio


@pytest.mark.live_llm
async def test_partial_hallucination_fields_marked(tmp_path):
    """合成仓库: 1 个真实 Java 实体 + 无其他文件 → agent 不应凭空编造实体/字段."""
    repo = tmp_path / "fake_repo"
    repo.mkdir()
    (repo / "User.java").write_text("""
package com.example;
import javax.persistence.*;

@Entity
@Table(name = "users")
public class User {
    @Id
    @GeneratedValue
    private Long id;

    @Column(name = "username", length = 50)
    private String username;

    @Column(name = "email")
    private String email;
}
""")
    result = await run_extraction_agent(repo_path=str(repo), hint_text=None, max_iterations=15)

    user_objs = [o for o in result.objects if "user" in o.get("name", "").lower()]
    assert len(user_objs) >= 1, \
        f"应发现 User 实体, 实际 objects: {[o.get('name') for o in result.objects]}"

    source = (repo / "User.java").read_text()
    declared = set()
    for m in re.finditer(r'@(?:Column|Id|JoinColumn)\s*\([^)]*name\s*=\s*"(\w+)"', source, re.I):
        declared.add(m.group(1))
    for m in re.finditer(r'private\s+\w+\s+(\w+);', source):
        declared.add(m.group(1))
    assert declared

    for obj in result.objects:
        assert obj.get("source_ref"), f"对象 {obj.get('name')} 无 source_ref"
        for f in obj.get("fields", []):
            fname = f.get("name") or f.get("field", "")
            if fname and not fname.startswith("_"):
                assert fname in declared, \
                    f"字段 '{obj['name']}.{fname}' 不在源码声明 {declared} 中 — 疑似编造"

    non_user = [o for o in result.objects if "user" not in o.get("name", "").lower()]
    assert len(non_user) == 0, \
        f"仓库只有 User.java, 不应产出其他实体: {[(o.get('name'), o.get('source_ref')) for o in non_user]}"
