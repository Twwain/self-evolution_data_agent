"""Agentic extractor vs PG 既有 schema 结构等价对比 (手动 G1 门, 非 CI).

用法 (CWD=backend, 需 .env LLM key):
    python -m scripts.verify_agentic_equivalence --repo data/repos/3 --namespace-id 1

对比通道:
  objects: {object_name → sorted(field_names)}  集合等价
  enums:   {(object, field) → sorted(enum_db_values)}  至少旧基线覆盖

注: data/repos/ 已被 .gitignore 覆盖, 本脚本在本地开发环境手动跑一次, 不入 CI。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _object_sets(objects: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for obj in objects:
        name = obj.get("name") or obj.get("table") or obj.get("collection", "?")
        fields: set[str] = set()
        for f in obj.get("fields", []):
            fields.add(f.get("name", ""))
            for sf in f.get("sub_fields", []):
                fields.add(f"{f.get('name', '?')}.{sf.get('name', '?')}")
        if name and fields:
            out[name] = {x for x in fields if x}
    return out


def _enum_sets(objects: list[dict]) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for obj in objects:
        oname = obj.get("name", "?")
        for f in obj.get("fields", []):
            evs = f.get("enum_values", [])
            if evs:
                out[(oname, f.get("name", ""))] = {str(e.get("db_value", "")) for e in evs}
    return out


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="repo path, e.g. data/repos/3")
    parser.add_argument("--namespace-id", type=int, default=1)
    parser.add_argument("--db-url", default=os.getenv("IS_METADATA_DB_URL", ""))
    args = parser.parse_args()

    repo_path = Path(args.repo).resolve()

    from app.knowledge.extraction_agent import run_extraction_agent
    result = await run_extraction_agent(repo_path=str(repo_path))
    print(f"Agentic: {len(result.objects)} objects, status={result.status}")

    db_url = args.db_url
    if not db_url:
        print("SKIP: no DB URL — cannot compare to PG baseline")
        return

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models.schema_canonical_object import SchemaCanonicalObject

    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        rows = (await conn.execute(
            select(SchemaCanonicalObject).where(
                SchemaCanonicalObject.namespace_id == args.namespace_id
            )
        )).fetchall()
    await engine.dispose()

    pg_objects = []
    for row in rows:
        fields = json.loads(row.fields_json) if row.fields_json else []
        pg_objects.append({"name": row.target, "fields": fields})
    print(f"PG baseline: {len(pg_objects)} objects")

    new_sets = _object_sets(result.objects)
    old_sets = _object_sets(pg_objects)

    diffs = []
    for name in sorted(set(new_sets) | set(old_sets)):
        nf, of = new_sets.get(name, set()), old_sets.get(name, set())
        missing, extra = of - nf, nf - of
        if missing or extra:
            diffs.append(f"  {name}: missing={missing}, extra={extra}")

    new_enums, old_enums = _enum_sets(result.objects), _enum_sets(pg_objects)
    for k in sorted(set(new_enums) | set(old_enums)):
        if old_enums.get(k, set()) - new_enums.get(k, set()):
            diffs.append(f"  ENUM MISS: {k}: old has {old_enums[k] - new_enums.get(k, set())}")

    if diffs:
        print("DIFFS:")
        for d in diffs:
            print(d)
        print("FAIL: 结构不等价")
        sys.exit(1)
    print("PASS: 结构等价")


if __name__ == "__main__":
    asyncio.run(main())
