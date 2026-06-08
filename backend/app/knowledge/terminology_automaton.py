"""AC 自动机术语精确匹配 — 替代向量检索用于 system prompt 业务术语锚点注入.

设计:
- per-namespace 缓存: 每个 ns 一棵 AC 自动机 (ns 自有 terminology + 全局 terminology)
- 去重: 同一 term 如果 ns 和全局都有, ns 优先 (全局的不加入自动机)
- 最长匹配: 后处理过滤被完全包含的短匹配
- 启动时加载: main.py lifespan 中调用 init_all_automatons()
- 增量失效: invalidate(namespace_id) 清缓存, rebuild 重建

性能:
- 构建: O(所有术语总字符数), 约 15ms (50 条 × 3 同义词)
- 匹配: O(用户问题长度), 约 0.02ms
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field

import ahocorasick
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace

log = logging.getLogger(__name__)


@dataclass
class _CachedAutomaton:
    """单个 namespace 的 AC 自动机缓存."""
    automaton: ahocorasick.Automaton
    entry_ids: set[int] = field(default_factory=set)


# ── 模块级缓存 ──
_cache: dict[str, _CachedAutomaton] = {}
_lock = threading.Lock()


# ════════════════════════════════════════════
#  构建
# ════════════════════════════════════════════

async def _build_automaton(db: AsyncSession, ns_id: int) -> _CachedAutomaton:
    """从 SQLite 加载 canonical terminology, 构建 AC 自动机.

    合并逻辑:
    - 先加载 ns 专属 terminology (namespace_id = ns_id)
    - 再加载全局 terminology (namespace_id IS NULL)
    - 去重: 同一 term 文本如果 ns 已有, 全局的跳过 (ns 优先)

    自动机 value 存储 (entry_id, pattern_length), 用于最长匹配后处理.
    """
    stmt = (
        select(
            KnowledgeEntry.id,
            KnowledgeEntry.namespace_id,
            KnowledgeEntry.payload,
        )
        .where(
            KnowledgeEntry.entry_type == "terminology",
            KnowledgeEntry.status == "canonical",
            KnowledgeEntry.is_superseded.is_(False),
        )
        .where(
            (KnowledgeEntry.namespace_id == ns_id)
            | (KnowledgeEntry.namespace_id.is_(None))
        )
    )
    res = await db.execute(stmt)
    rows = res.all()

    # 分离 ns 专属 vs 全局
    ns_entries: list[tuple[int, dict]] = []
    global_entries: list[tuple[int, dict]] = []
    for entry_id, namespace_id, payload_str in rows:
        try:
            payload = json.loads(payload_str or "{}")
        except json.JSONDecodeError:
            continue
        if not payload.get("term"):
            continue
        if namespace_id == ns_id:
            ns_entries.append((entry_id, payload))
        else:
            global_entries.append((entry_id, payload))

    # 构建自动机
    # value = (entry_id, pattern_length) — 用于最长匹配后处理
    # 同一 pattern 可能被多个 entry 注册 (如 "题" 同时是多个 entry 的同义词)
    # ahocorasick 同 key 后加覆盖前面的, 所以 ns 先加, 全局后加时同 pattern ns 优先
    automaton = ahocorasick.Automaton()
    entry_ids: set[int] = set()
    ns_terms: set[str] = set()

    def _add_entry(entry_id: int, payload: dict) -> None:
        term = payload["term"]
        synonyms = list(payload.get("synonyms") or [])
        patterns = [term] + [s for s in synonyms if s]
        for pattern in patterns:
            automaton.add_word(pattern, (entry_id, len(pattern)))
        entry_ids.add(entry_id)

    # 先加 ns 专属
    for entry_id, payload in ns_entries:
        _add_entry(entry_id, payload)
        ns_terms.add(payload["term"])

    # 再加全局 (同名 term 跳过)
    for entry_id, payload in global_entries:
        if payload["term"] in ns_terms:
            continue
        _add_entry(entry_id, payload)

    if len(automaton) > 0:
        automaton.make_automaton()

    return _CachedAutomaton(automaton=automaton, entry_ids=entry_ids)


# ════════════════════════════════════════════
#  最长匹配后处理
# ════════════════════════════════════════════

def _longest_match_filter(
    raw_matches: list[tuple[int, int, int]],
) -> list[int]:
    """从 AC 原始匹配中过滤出最长匹配, 返回去重的 entry_id 列表.

    raw_matches: [(start_idx, end_idx, entry_id), ...]
    规则: 如果匹配 A 的区间 [startA, endA] 被匹配 B 的区间 [startB, endB] 完全包含
          (startB <= startA and endA <= endB and (startA, endA) != (startB, endB)),
          则丢弃 A.
    """
    if not raw_matches:
        return []

    # 按区间长度降序排列, 长的优先保留
    sorted_matches = sorted(
        raw_matches, key=lambda m: m[1] - m[0], reverse=True,
    )

    kept: list[tuple[int, int, int]] = []
    for start, end, entry_id in sorted_matches:
        # 检查是否被已保留的更长匹配完全包含
        is_covered = any(
            ks <= start and end <= ke
            for ks, ke, _ in kept
        )
        if not is_covered:
            kept.append((start, end, entry_id))

    # 去重 entry_id, 保持首次出现顺序 (按文本中出现位置排序)
    kept.sort(key=lambda m: m[0])
    seen: set[int] = set()
    result: list[int] = []
    for _, _, entry_id in kept:
        if entry_id not in seen:
            seen.add(entry_id)
            result.append(entry_id)
    return result


# ════════════════════════════════════════════
#  公开 API
# ════════════════════════════════════════════

def match_terminology(ns_slug: str, question: str) -> list[int]:
    """从用户问题中精确匹配术语, 返回去重的 entry_id 列表 (最长匹配).

    如果该 ns 的自动机尚未构建 (缓存被 invalidate 后未重建), 返回空列表.
    调用方应在启动时确保 init_all_automatons() 已执行.
    """
    with _lock:
        cached = _cache.get(ns_slug)
    if cached is None:
        return []
    if len(cached.automaton) == 0:
        return []

    # AC 自动机遍历: 收集所有匹配
    # iter 返回 (end_idx_inclusive, value), value = (entry_id, pattern_length)
    raw_matches: list[tuple[int, int, int]] = []
    for end_idx, (entry_id, pattern_len) in cached.automaton.iter(question):
        start_idx = end_idx - pattern_len + 1
        raw_matches.append((start_idx, end_idx, entry_id))

    return _longest_match_filter(raw_matches)


async def invalidate(namespace_id: int | None) -> None:
    """清除自动机缓存.

    namespace_id=None (全局术语变更) → 清除所有 ns 的缓存.
    namespace_id=X → 清除所有缓存 (ns 数量少, 全量重建成本低于维护 id→slug 映射).

    调用方应在 invalidate 后调用 rebuild 重建, 或等待下次 match 返回空触发感知.
    """
    with _lock:
        if namespace_id is None:
            _cache.clear()
            log.info("[automaton] invalidate all namespaces")
        else:
            _cache.clear()
            log.info("[automaton] invalidate namespace_id=%d (cleared all)", namespace_id)


async def rebuild(db: AsyncSession, ns_id: int, ns_slug: str) -> None:
    """显式重建单个 namespace 的自动机并写入缓存."""
    cached = await _build_automaton(db, ns_id)
    with _lock:
        _cache[ns_slug] = cached
    log.info(
        "[automaton] rebuilt ns=%s entries=%d patterns=%d",
        ns_slug, len(cached.entry_ids), len(cached.automaton),
    )


async def rebuild_all(db: AsyncSession) -> None:
    """重建所有 namespace 的自动机 (invalidate 后批量重建)."""
    stmt = select(Namespace.id, Namespace.slug)
    res = await db.execute(stmt)
    namespaces = res.all()
    for ns_id, ns_slug in namespaces:
        await rebuild(db, ns_id, ns_slug)


async def init_all_automatons(db: AsyncSession) -> None:
    """启动时为所有 namespace 构建自动机."""
    stmt = select(Namespace.id, Namespace.slug)
    res = await db.execute(stmt)
    namespaces = res.all()
    for ns_id, ns_slug in namespaces:
        await rebuild(db, ns_id, ns_slug)
    log.info("[automaton] initialized %d namespace automatons", len(namespaces))
