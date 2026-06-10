"""批量解决 table_description 冲突 — 选非空描述候选 (keep_b 语义).

背景: MySQLDriver 单表分支历史 bug 导致 introspect 写入空表描述候选,
修复后新刷新产生正确非空候选, 两者冲突。本脚本对每个 open
table_description 冲突, 选「非空描述」候选为 winner, 复用端点同款流程
(winner→pending, losers→rejected, conflict→resolved, promote_single_field)。

复用 app/api/schema_canonical_v2.py::resolve_conflict 的核心逻辑, 不经 HTTP。
"""
import asyncio
import json
from datetime import datetime

from sqlalchemy import select

from app.db.metadata import async_session
from app.knowledge.canonical_audit import write_canonical_audit_log
from app.knowledge.canonical_promote import promote_single_field
from app.models import SchemaCanonicalCandidate, SchemaCanonicalConflict


async def main():
    async with async_session() as db:
        conflicts = (await db.execute(
            select(SchemaCanonicalConflict).where(
                SchemaCanonicalConflict.candidate_kind == "table_description",
                SchemaCanonicalConflict.status == "open",
            )
        )).scalars().all()
        print(f"== 待解决 open table_description 冲突 = {len(conflicts)} ==")

        resolved, skipped = 0, 0
        for conf in conflicts:
            candidate_ids = json.loads(conf.candidate_ids_json)
            cands = list((await db.execute(
                select(SchemaCanonicalCandidate).where(
                    SchemaCanonicalCandidate.id.in_(candidate_ids)
                )
            )).scalars().all())

            # 选非空描述候选为 winner
            nonempty = [
                c for c in cands
                if json.loads(c.candidate_value_json).get("description", "").strip()
            ]
            if not nonempty:
                print(f"  SKIP target={conf.target} (无非空候选)")
                skipped += 1
                continue
            # 多个非空时取最新写入的 (id 最大)
            winner = max(nonempty, key=lambda c: c.id)
            losers = [c for c in cands if c.id != winner.id]

            winner.status = "pending"
            for loser in losers:
                loser.status = "rejected"
                loser.rejected_at = datetime.now()

            conf.status = "resolved"
            conf.resolution_choice = "keep_b"
            conf.resolved_by = None
            conf.resolved_at = datetime.now()
            conf.resolution_reason = "batch_fix_empty_table_desc_bug"

            await write_canonical_audit_log(
                db, namespace_id=conf.namespace_id,
                action="conflict_resolve_keep_b",
                conflict_id=conf.id, field_path=conf.field_path,
                actor_id=None, reason="batch_fix_empty_table_desc_bug",
            )
            await db.flush()

            await promote_single_field(
                db, ns_id=conf.namespace_id, db_type=conf.db_type,
                database=conf.database, target=conf.target,
                field_path=conf.field_path, candidate_kind=conf.candidate_kind,
            )
            resolved += 1

        await db.commit()
        print(f"\n== 完成: resolved={resolved}, skipped={skipped} ==")


asyncio.run(main())
