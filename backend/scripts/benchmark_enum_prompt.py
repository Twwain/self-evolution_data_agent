"""
Enum 抽取 + 启发式命中率 benchmark.
对 backend/data/repos/{3,5,6,7} 跑完整解析, 输出 JSON 报告.

USAGE:
    cd backend && python scripts/benchmark_enum_prompt.py

Exit code: 0 命中率达标 / 1 总命中率 < 22%
"""
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

from app.knowledge.code_parser import (  # noqa: E402
    _build_class_index,
    _scan_dao_collection_mappings,
    _scan_document_classes,
    _scan_files,
)
from app.knowledge.enum_extractor import (  # noqa: E402
    BASE_TYPES,
    EnumDef,
    EnumValue,
    _field_root_and_suffix,
    _resolve_enum_class,
)

RE_FIELD = re.compile(r"private\s+(\w+(?:<[^>]+>)?)\s+(\w+)\s*;")
RE_ENUM = re.compile(r"^\s*public\s+enum\s+(\w+)", re.M)

THRESHOLD_MATCH_RATE = 22.0  # noqa: hardcode


def benchmark_repo(repo_id: int) -> dict[str, Any]:
    """单 repo benchmark, 返回统计 dict."""
    repo = f"data/repos/{repo_id}"
    if not Path(repo).exists():
        return {"repo_id": repo_id, "skipped": "no local copy"}

    java_files, _ = _scan_files(repo)
    annotated = _scan_document_classes(java_files)
    dao_map, dao_refs = _scan_dao_collection_mappings(java_files)
    _, simple_index = _build_class_index(java_files)

    seed_paths: set[str] = set()
    hidden: dict[str, str] = {}
    for cls in dao_refs - annotated:
        coll = dao_map.get(cls)
        paths = simple_index.get(cls, [])
        if coll and paths:
            hidden[cls] = coll
    for cls in annotated:
        for p in simple_index.get(cls, []):
            seed_paths.add(p)
    for cls in hidden:
        for p in simple_index.get(cls, []):
            seed_paths.add(p)

    # Build enum_class_index from regex scan
    enum_class_index: dict[str, EnumDef] = {}
    for jf in java_files:
        try:
            text = Path(jf).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name in RE_ENUM.findall(text):
            enum_class_index[name] = EnumDef(
                enum_class=name,
                fully_qualified_name=name,
                values=[EnumValue(name="X", db_value=0, description="")],
            )

    cand = matched = 0
    samples: list[dict[str, Any]] = []
    by_source: dict[str, int] = {}
    for seed in seed_paths:
        try:
            text = Path(seed).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in RE_FIELD.finditer(text):
            ftype, fname = m.group(1), m.group(2)
            field = {"type": ftype, "field": fname}
            _, f_suf = _field_root_and_suffix(fname)
            inner = re.sub(r"<.*>", "", ftype)
            if inner not in BASE_TYPES or not f_suf:
                continue
            cand += 1
            ec, src = _resolve_enum_class(field, enum_class_index)
            if ec:
                matched += 1
                by_source[src] = by_source.get(src, 0) + 1
                if len(samples) < 10:
                    samples.append({
                        "field": fname, "type": ftype,
                        "matched": ec, "source": src,
                    })

    return {
        "repo_id": repo_id,
        "java_files": len(java_files),
        "enum_classes": len(enum_class_index),
        "seeds": len(seed_paths),
        "candidates": cand,
        "matched": matched,
        "match_rate": round(matched / cand * 100, 2) if cand else 0,
        "by_source": by_source,
        "samples": samples,
    }


def main() -> int:
    repos = [3, 5, 6, 7]
    report: dict[str, Any] = {"repos": [benchmark_repo(r) for r in repos]}

    active = [r for r in report["repos"] if "candidates" in r]
    total_cand = sum(r["candidates"] for r in active)
    total_matched = sum(r["matched"] for r in active)
    report["total"] = {
        "candidates": total_cand,
        "matched": total_matched,
        "match_rate": round(total_matched / total_cand * 100, 2) if total_cand else 0,
    }

    # 输出 JSON 报告
    out = Path("data/enum_benchmark.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # 人类可读摘要
    print("=" * 60)
    print("Enum Heuristic Benchmark Report")
    print("=" * 60)
    for r in report["repos"]:
        if "skipped" in r:
            print(f"  repo {r['repo_id']}: SKIPPED ({r['skipped']})")
        else:
            print(
                f"  repo {r['repo_id']}: "
                f"{r['matched']}/{r['candidates']} = {r['match_rate']}%"
            )
    print("-" * 60)
    print(
        f"  TOTAL: {total_matched}/{total_cand} = "
        f"{report['total']['match_rate']}%"
    )
    print("=" * 60)

    # 命中率门
    if report["total"]["match_rate"] < THRESHOLD_MATCH_RATE:
        print(
            f"\nFAIL: total match rate {report['total']['match_rate']}% "
            f"< {THRESHOLD_MATCH_RATE}%",
            file=sys.stderr,
        )
        return 1

    print(f"\nPASS: match rate >= {THRESHOLD_MATCH_RATE}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
