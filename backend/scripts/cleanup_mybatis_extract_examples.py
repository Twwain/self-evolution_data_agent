"""Stage A — 一次性清理 mybatis_extract example KE.

走 BulkOperationGuard 留 audit_log + ChromaDB best-effort 同步.

幂等: 重跑命中 0 行即 noop.

Usage:
    cd backend && python -m scripts.cleanup_mybatis_extract_examples            # 真删
    cd backend && python -m scripts.cleanup_mybatis_extract_examples --dry-run  # 仅预览

Spec: docs/superpowers/specs/2026-05-25-mysql-canonical-pull-and-mybatis-example-deprecation
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv(".env")

from sqlalchemy import select  # noqa: E402

from app.db.metadata import async_session  # noqa: E402
from app.knowledge.bulk_guard import BulkOperationGuard  # noqa: E402
from app.models.namespace import Namespace  # noqa: E402

log = logging.getLogger(__name__)


async def main(dry_run: bool, ns_slug: str | None) -> int:
    async with async_session() as db:
        ns_rows = list((await db.execute(select(Namespace))).scalars().all())
        if not ns_rows:
            print("no namespace, noop")
            return 0

        total_affected = 0
        for ns in ns_rows:
            if ns_slug and ns.slug != ns_slug:
                continue
            guard = BulkOperationGuard(
                op_name="stage_a_mybatis_example_deprecation",
                scope_filter={
                    "source": ["mybatis_extract"],
                    "entry_type": ["example"],
                    "namespace_id": ns.id,
                },
                dry_run=dry_run,
                actor_id=None,
                reason="Stage A spec 2026-05-25: mybatis_extract example 产线下线",
            )
            report = await guard.execute(db, slug=ns.slug)
            print(
                f"ns={ns.slug} affected={report.affected_count} "
                f"preserved_audited={report.preserved_audited_count} "
                f"sample={report.sample_ids[:5]}"
            )
            total_affected += report.affected_count
        print(f"\nTOTAL affected={total_affected} dry_run={dry_run}")
        return total_affected


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ns", default=None, help="restrict to a namespace slug")
    args = parser.parse_args()
    rc = asyncio.run(main(dry_run=args.dry_run, ns_slug=args.ns))
    sys.exit(0 if rc >= 0 else 1)
