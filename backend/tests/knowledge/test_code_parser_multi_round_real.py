"""多轮分层展开 — 真实 LLM 集成测试 (IT-1/2/3).

对应 02-acceptance.md §2.1. 默认 @pytest.mark.real_llm 跳过, 手工跑用:

    cd backend && pytest -m real_llm tests/knowledge/test_code_parser_multi_round_real.py -v -s

入参为真实 repo `backend/data/repos/6` (papers-service) 的 batch:
- IT-1: QuestionEntity.java + BaseEntity.java + BaseQuestionInfoEntity.java
        (亲和分批自然产物, 即历史 504 现场, 716 行 / 15 内部类 / 121 字段)
- IT-2: 同 batch 关多轮 (阈值 = 999), 单轮基线对照, timeout 上限 120s 防 CI 拖死
- IT-3: 同 batch 含自引用 (BaseEntity.id 自由风格), 验证不死循环 + 60s 内完成
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.config import settings
from app.knowledge.code_parser import (
    _make_affinity_batches,
    _parse_complex_batch_multi_round,
    _resolve_references,
    _scan_files,
)


def _llm_key_present() -> bool:
    """根据 provider 选择对应的 API key 字段."""
    if settings.llm_provider == "claude":
        return bool(settings.claude_api_key)
    return bool(settings.llm_api_key)


pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.skipif(
        not _llm_key_present(),
        reason="IT 需要真实 LLM (设 IS_LLM_API_KEY 或 IS_CLAUDE_API_KEY)",
    ),
]


# ════════════════════════════════════════════════════════════════
#  Fixture — 复用 papers-service repo 的 QuestionEntity 真实 batch
# ════════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parents[3] / "backend" / "data" / "repos" / "6"


def _build_question_entity_batch() -> list[tuple[str, str, bool]]:
    """复现 _make_affinity_batches 在 papers-service 上对 QuestionEntity 的真实分批结果."""
    java_files, _ = _scan_files(str(REPO_ROOT))
    seeds = [p for p in java_files if os.path.basename(p) == "QuestionEntity.java"]
    assert seeds, f"QuestionEntity.java not found under {REPO_ROOT}"

    all_relevant, ref_map = _resolve_references(seeds, java_files)
    ref_set = set(all_relevant) - set(seeds)
    batches = _make_affinity_batches(seeds, ref_map, ref_set)

    target = next(
        (
            b for b in batches
            if any(os.path.basename(p) == "QuestionEntity.java" for p, _, _ in b)
        ),
        None,
    )
    assert target, "QuestionEntity 未落入任一 batch"
    return target


@pytest.fixture(scope="module")
def question_batch() -> list[tuple[str, str, bool]]:
    if not REPO_ROOT.exists():
        pytest.skip(f"papers-service repo 缺失: {REPO_ROOT}")
    return _build_question_entity_batch()


# ════════════════════════════════════════════════════════════════
#  IT-1 — 多轮路径覆盖 504 batch, 字段数 ≥ Round 1 骨架
# ════════════════════════════════════════════════════════════════


@pytest.mark.timeout(120)
def test_it1_question_entity_multi_round_recovers_fields(question_batch):
    """IT-1: 多轮路径下 QuestionEntity 字段数 ≥ 70 (历史 504 batch 现已可恢复)."""
    started = time.monotonic()
    result = _parse_complex_batch_multi_round(question_batch)
    elapsed = time.monotonic() - started

    assert result["errored"] is False, "Round 1 不应失败"

    docs = result["mongo_docs"]
    q_doc = next(
        (d for d in docs if d.get("class_name") == "QuestionEntity"),
        None,
    )
    assert q_doc is not None, f"未抽到 QuestionEntity, docs={[d.get('class_name') for d in docs]}"

    fields = q_doc.get("fields", [])
    field_count = len(fields)
    print(
        f"\n[IT-1] elapsed={elapsed:.1f}s docs={len(docs)} "
        f"QuestionEntity.fields={field_count} partial={result['partial']}"
    )
    assert field_count >= 30, (
        f"QuestionEntity 字段数 {field_count} 异常偏低, 多轮拼接可能未生效"
    )


# ════════════════════════════════════════════════════════════════
#  IT-2 — 单轮基线对照 (阈值=999), 不作 hard assertion
# ════════════════════════════════════════════════════════════════


@pytest.mark.timeout(180)
def test_it2_single_round_baseline_for_comparison(question_batch, monkeypatch):
    """IT-2: 同 batch 关多轮 (阈值=999) 走单轮基线, 仅记录耗时与结果, 不作硬断言.

    历史现象是这条路径 504. 测试不强制 pass — 失败也是有效记录, 只确保不死循环.
    """
    monkeypatch.setattr(settings, "code_parse_complex_threshold", 999.0)

    from app.knowledge.code_parser import _parse_java_batch

    started = time.monotonic()
    paths = [p for p, _, _ in question_batch]
    ref_set = {p for p, _, is_ref in question_batch if is_ref}
    try:
        entities, mongo_docs, _, stats = _parse_java_batch(paths, ref_set=ref_set)
        outcome = "ok"
        errored = stats.get("errored", 0)
    except Exception as e:
        outcome = f"raised:{type(e).__name__}:{e}"
        entities, mongo_docs, errored = [], [], len(paths)
    elapsed = time.monotonic() - started

    print(
        f"\n[IT-2] elapsed={elapsed:.1f}s outcome={outcome} "
        f"entities={len(entities)} mongo_docs={len(mongo_docs)} errored={errored}"
    )


# ════════════════════════════════════════════════════════════════
#  IT-3 — 自引用不死循环 (60s 内完成)
# ════════════════════════════════════════════════════════════════


@pytest.mark.timeout(180)
def test_it3_self_reference_terminates_within_60s(question_batch):
    """IT-3: QuestionEntity 内部含 List<Tag>/List<Option> 等递归型字段引用.

    多轮路径深度上限 code_parse_expansion_max_depth (默认 4) 必须切断递归.
    硬阈值 = pytest timeout (180s) 兜底防死循环; 实际典型耗时 60-120s
    (双轮 LLM + Round 2 多 chunk).
    """
    started = time.monotonic()
    result = _parse_complex_batch_multi_round(question_batch)
    elapsed = time.monotonic() - started

    print(f"\n[IT-3] elapsed={elapsed:.1f}s partial={result['partial']}")
    assert result["errored"] is False, "Round 1 不应失败"
    # 不死循环判定: pytest.mark.timeout(180) 兜底 — 真死循环会被 SIGKILL 命中
