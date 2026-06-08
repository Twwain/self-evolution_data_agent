"""5 类 relationship 信号抽取与聚合 (Phase 2 Task 5).

设计: 03-extraction.md §3.3
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def aggregate_relationships(
    *,
    mysql_fks: list[dict[str, Any]],
    jpa_entities: list[dict[str, Any]],
    mongo_documents: list[dict[str, Any]],
    mybatis_joins: list[dict[str, Any]],
    dao_relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """5 类信号汇聚 + 阈值过滤. 输出统一 dict list.

    显式信号 (always include): MySQL FK, JPA relations, Mongo @DBRef/@Reference
    弱信号 (threshold filter): MyBatis JOIN, DAO app join
    """
    rels: list[dict[str, Any]] = []

    # ── 显式 1: MySQL FK (pass-through) ──
    rels.extend(mysql_fks)

    # ── 显式 2: JPA @OneToMany / @ManyToOne / @JoinColumn ──
    for ent in jpa_entities:
        for r in ent.get("relations") or []:
            rels.append({
                "from_db_type": "mysql",
                "from_database": ent.get("database"),
                "from_target": ent.get("table"),
                "from_field": r.get("field"),
                "to_db_type": "mysql",
                "to_database": ent.get("database"),
                "to_target": r.get("target"),
                "to_field": r.get("to_field"),
                "relation_type": r.get("kind", "many_to_one"),
                "is_required": r.get("is_required", False),
                "evidence": [{"source": "code_jpa_relation", "javadoc": r.get("javadoc")}],
            })

    # ── 显式 3: Mongo @DBRef / @Reference / DBRef typed ──
    for doc in mongo_documents:
        for f in doc.get("fields") or []:
            ref_target = f.get("ref_target")
            ref_kind = f.get("ref_kind")
            if ref_target and ref_kind:
                rels.append({
                    "from_db_type": "mongodb",
                    "from_database": doc.get("database"),
                    "from_target": doc.get("collection"),
                    "from_field": f.get("name"),
                    "to_db_type": "mongodb",
                    "to_database": doc.get("database"),
                    "to_target": ref_target,
                    "to_field": "_id",
                    "relation_type": "many_to_one",
                    "is_required": f.get("nullable") is False,
                    "evidence": [{"source": ref_kind}],
                })

    # ── 弱 1: MyBatis JOIN 阈值过滤 ──
    hit_threshold = settings.relationship_join_hit_threshold
    mapper_threshold = settings.relationship_join_mapper_threshold

    by_key: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for j in mybatis_joins:
        key = (j["from_target"], j["from_field"], j["to_target"], j["to_field"])
        by_key[key].append(j)

    for key, hits in by_key.items():
        total = sum(h.get("hit_count", 1) for h in hits)
        mappers = {h["mapper"] for h in hits}
        if total >= hit_threshold and len(mappers) >= mapper_threshold:
            rels.append({
                "from_db_type": "mysql",
                "from_target": key[0],
                "from_field": key[1],
                "to_db_type": "mysql",
                "to_target": key[2],
                "to_field": key[3],
                "relation_type": "many_to_one",
                "is_required": False,
                "evidence": [{
                    "source": "usage_join_pattern",
                    "hit_count": total,
                    "mappers": sorted(mappers),
                }],
            })

    # ── 弱 2: DAO 应用双查询 阈值过滤 ──
    dao_hit_threshold = settings.usage_implicit_id_ref_hit_threshold
    by_key_dao: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in dao_relations:
        key = (r.get("from_target"), r.get("from_field"),
               r.get("to_target"), r.get("to_field"))
        by_key_dao[key].append(r)

    for key, hits in by_key_dao.items():
        if len(hits) >= dao_hit_threshold:
            rels.append({
                "from_db_type": hits[0].get("from_db_type", "unknown"),
                "from_target": key[0],
                "from_field": key[1],
                "to_db_type": hits[0].get("to_db_type", "unknown"),
                "to_target": key[2],
                "to_field": key[3],
                "relation_type": "many_to_one",
                "is_required": False,
                "evidence": [{
                    "source": "usage_app_join",
                    "hit_count": len(hits),
                    "patterns": [h.get("evidence", {}).get("pattern") for h in hits],
                }],
            })

    return rels


async def extract_dao_relationships(
    dao_files: list[str],
    *,
    entity_summary: dict[str, list[str]],
    db=None,
    namespace_id: int | None = None,
) -> list[dict[str, Any]]:
    """弱 2: DAO 应用双查询 / lookup pipeline / manual_id_filter / dbref deref.

    LLM 看 DAO 类源码 + 引用 entity 简要 schema, 输出 5 类语义 join.
    """
    import asyncio

    from app.engine.llm import chat_completion
    from app.knowledge.extraction_prompts import load_prompt_or_fallback
    from app.knowledge.llm_retry import with_retry

    template_body = load_prompt_or_fallback("02-relationship-dao-extraction")

    out: list[dict[str, Any]] = []
    for fp in dao_files:
        try:
            src = Path(fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            logger.warning("DAO 文件读取失败: %s", fp)
            continue

        prompt = template_body.replace("{dao_source}", src).replace(
            "{entity_schema_context}", json.dumps(entity_summary, ensure_ascii=False)
        )

        try:
            raw = await with_retry(
                lambda: asyncio.to_thread(chat_completion, [{"role": "user", "content": prompt}]),
                template_name="relationship_dao_extraction",
                input_meta={"file": fp},
                prompt_full=prompt,
                extraction_kind="relationship",
                db=db,
                namespace_id=namespace_id,
            )
            from app.engine.json_parser import parse_llm_json
            parsed = parse_llm_json(raw, expect="dict")
            if parsed is None:
                logger.warning("DAO relationship 抽取 JSON 解析失败 %s", fp)
                continue
            for rel in parsed.get("relationships", []):
                out.append(rel)
        except (KeyError, Exception) as e:
            logger.warning("DAO relationship 抽取失败 %s: %s", fp, e)
            continue

    return out
