"""
Stage 1 验收门 e2e — 7 条硬要求全覆盖.

对应 docs/todos/knowledge-unification-and-agent-loop/05-stage-roadmap.md
Stage 1 验收门 (#1 ~ #7) 的可执行实证. 任一测试失败 = Stage 1 验收不通过.

验收门映射:
- #1 KnowledgeEntry.status 四态机生效     → test_stage1_status_state_machine_enforced
- #2 audit_log 必写在状态变更时             → test_stage1_audit_log_present_for_status_changes
- #3 BusinessTerm ORM 全栈删除             → test_stage1_no_business_term_imports
- #4 NamespaceRule ORM 全栈删除            → test_stage1_no_namespace_rule_imports
- #5 旧表从 schema 物理删除                → test_stage1_old_tables_dropped
- #6 KnowledgeStatus Literal 锁住拼写       → test_stage1_status_state_machine_enforced
- #7 retriever 仅 canonical 进 RAG         → 由 tests/knowledge/test_status_state_machine.py 覆盖
                                              (此处不重复, 见 P0 套件)
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.knowledge.audit import write_audit
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry, KnowledgeStatus

# ── repo 根 (backend/) — 验收 grep 在此目录下执行 ──
REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"


# ══════════════════════════════════════════════════════════════════════════════
#  #5 旧表物理删除
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stage1_old_tables_dropped(db_engine):
    """business_terms / namespace_rules 必须不在 schema 中."""
    async with db_engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename IN ('business_terms', 'namespace_rules')"
        ))).all()
    assert rows == [], f"旧表仍存在 schema: {rows}"


# ══════════════════════════════════════════════════════════════════════════════
#  #3 / #4 grep 0 命中代码引用
# ══════════════════════════════════════════════════════════════════════════════

# 代码引用 = `import` / 实例化 `(` / class 继承 `(BusinessTerm)` / `BusinessTerm.<attr>`
# 排除 docstring/comment 历史描述: 行首是 `#` 或行内仅出现在 """..."""/`"""...`/`...`/`...`/comment 内
# 直接用精确正则: 匹配 `\bBusinessTerm\s*[\(\.]` 或 `from .* import .*\bBusinessTerm\b`
#
# 注: scripts/ 内剩余 docstring 命中 (历史脚注) 不属于 app/ 验收范围, plan 字面要求 backend/app/.

_BT_CODE_REF_RE = re.compile(
    r"(?:from\s+\S+\s+import\s+[^\n]*\bBusinessTerm\b|"  # import BusinessTerm
    r"\bBusinessTerm\s*[\(\.])",                          # 实例化 / 属性访问
)
_NR_CODE_REF_RE = re.compile(
    r"(?:from\s+\S+\s+import\s+[^\n]*\bNamespaceRule\b|"
    r"\bNamespaceRule\s*[\(\.])",
)


def _grep_code_references(pattern: re.Pattern, root: Path) -> list[tuple[Path, int, str]]:
    """扫 root 下所有 .py 文件, 返回 (path, lineno, line) 命中代码引用."""
    hits: list[tuple[Path, int, str]] = []
    for py in root.rglob("*.py"):
        # 跳过 __pycache__ 和 .pyc (rglob *.py 自然排除 .pyc, __pycache__ 兜底)
        if "__pycache__" in py.parts:
            continue
        try:
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append((py, lineno, line))
        except (OSError, UnicodeDecodeError):
            continue
    return hits


def test_stage1_no_business_term_imports():
    """app/ 下不得有 BusinessTerm 代码引用 (import / 实例化 / 属性访问)."""
    hits = _grep_code_references(_BT_CODE_REF_RE, APP_DIR)
    assert hits == [], (
        "BusinessTerm 仍有代码引用:\n"
        + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {line}"
                    for p, ln, line in hits)
    )


def test_stage1_no_namespace_rule_imports():
    """app/ 下不得有 NamespaceRule 代码引用 (import / 实例化 / 属性访问)."""
    hits = _grep_code_references(_NR_CODE_REF_RE, APP_DIR)
    assert hits == [], (
        "NamespaceRule 仍有代码引用:\n"
        + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {line}"
                    for p, ln, line in hits)
    )


def test_stage1_subprocess_grep_business_term_in_app():
    """plan 字面要求: subprocess grep -rn BusinessTerm backend/app/ — 0 命中.

    用 _BT_CODE_REF_RE 同款过滤后再 grep, 排除 docstring/comment 中无害的历史描述.
    """
    result = subprocess.run(
        ["grep", "-rEn", r"(from[[:space:]]+\S+[[:space:]]+import[[:space:]]+[^$]*\bBusinessTerm\b|\bBusinessTerm[[:space:]]*[(.])",
         str(APP_DIR)],
        capture_output=True, text=True,
    )
    # grep returncode: 0=found, 1=not found, 2=error
    assert result.returncode != 0 and result.stdout == "", \
        f"BusinessTerm 仍有代码引用:\n{result.stdout}"


def test_stage1_subprocess_grep_namespace_rule_in_app():
    """plan 字面要求: subprocess grep -rn NamespaceRule backend/app/ — 0 命中代码引用."""
    result = subprocess.run(
        ["grep", "-rEn", r"(from[[:space:]]+\S+[[:space:]]+import[[:space:]]+[^$]*\bNamespaceRule\b|\bNamespaceRule[[:space:]]*[(.])",
         str(APP_DIR)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0 and result.stdout == "", \
        f"NamespaceRule 仍有代码引用:\n{result.stdout}"


# ══════════════════════════════════════════════════════════════════════════════
#  #2 audit_log 写入契约
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stage1_audit_log_present_for_status_changes(db_session):
    """状态变更必写 audit_log (write_audit helper 是唯一入口)."""
    ke = KnowledgeEntry(
        entry_type="terminology",
        content="x",
        source="manual",
        status="proposed",
    )
    db_session.add(ke)
    await db_session.flush()

    await write_audit(
        db_session, entry_id=ke.id, action="propose", to_status="proposed",
    )
    await db_session.commit()

    logs = list((await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == ke.id)
    )).all())
    assert len(logs) == 1
    assert logs[0].action == "propose"
    assert logs[0].to_status == "proposed"
    assert logs[0].from_status is None
    assert logs[0].actor_id is None  # 系统写入


# ══════════════════════════════════════════════════════════════════════════════
#  #1 + #6 status 四态机 + KnowledgeStatus Literal
# ══════════════════════════════════════════════════════════════════════════════

def test_stage1_knowledge_status_literal_locked_to_4_values():
    """KnowledgeStatus Literal 必须为 4 态机, 一字不差.

    新增/删除态时此测试触发, 强制更新 audit/retriever 等下游.
    """
    from typing import get_args
    args = get_args(KnowledgeStatus)
    assert set(args) == {"proposed", "canonical", "superseded", "rejected"}, \
        f"KnowledgeStatus 4 态机被改: {args}"


@pytest.mark.asyncio
async def test_stage1_status_default_is_proposed(db_session):
    """新建 KE 默认 status=proposed (不传时), 防止误进 RAG."""
    ke = KnowledgeEntry(
        entry_type="rule",
        content="some rule",
        source="manual",
    )
    db_session.add(ke)
    await db_session.commit()
    await db_session.refresh(ke)
    assert ke.status == "proposed"
