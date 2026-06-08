#!/usr/bin/env python3
"""
pyright baseline 比对器 — CI 用.

策略: 锁定当前 errors 入 .pyright-baseline.txt, 新增 0 容忍.
- 当前 errors == baseline → exit 0
- 新增 errors → exit 1, 打印 diff
- 减少 errors → exit 0 + 提示更新 baseline

更新 baseline: ./scripts/pyright_baseline.py --regenerate
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".pyright-baseline.txt"


def collect_errors() -> set[str]:
    """跑 pyright, 返回 {file:line:rule} 集合."""
    proc = subprocess.run(
        ["pyright", "--outputjson", "backend/app"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if not proc.stdout:
        sys.stderr.write(f"pyright failed: {proc.stderr}\n")
        sys.exit(2)

    data = json.loads(proc.stdout)
    out: set[str] = set()
    for e in data.get("generalDiagnostics", []):
        if e["severity"] != "error":
            continue
        f = e["file"].replace(str(ROOT) + "/", "")
        line = e["range"]["start"]["line"] + 1
        rule = e.get("rule", "")
        out.add(f"{f}:{line}:{rule}")
    return out


def load_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    out: set[str] = set()
    for line in BASELINE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "baseline_count=")):
            continue
        out.add(line)
    return out


def write_baseline(errors: set[str]) -> None:
    sorted_errs = sorted(errors)
    BASELINE.write_text(
        f"baseline_count={len(sorted_errs)}\n" + "\n".join(sorted_errs) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--regenerate", action="store_true",
        help="重写 baseline 为当前状态 (清零或减少时使用)",
    )
    args = parser.parse_args()

    current = collect_errors()
    baseline = load_baseline()

    if args.regenerate:
        write_baseline(current)
        print(f"✓ baseline 已更新: {len(current)} errors")
        return 0

    new_errors = current - baseline
    fixed_errors = baseline - current

    if new_errors:
        print(f"✘ 新增 {len(new_errors)} 条 pyright error (baseline 锁 {len(baseline)} 条):")
        for e in sorted(new_errors):
            print(f"  + {e}")
        print()
        print("修复后再提交; 若属意外既有错入 baseline, 跑:")
        print("  python scripts/pyright_baseline.py --regenerate")
        return 1

    if fixed_errors:
        print(f"✓ 修复了 {len(fixed_errors)} 条既有 error, 请更新 baseline:")
        for e in sorted(fixed_errors):
            print(f"  - {e}")
        print()
        print("跑: python scripts/pyright_baseline.py --regenerate")
        return 1

    print(f"✓ pyright OK ({len(current)} errors == baseline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
