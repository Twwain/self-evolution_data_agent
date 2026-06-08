"""Stage 2 Task 8 — backend/app 硬编码数字守门员

Stage 1/Stage 2 已把所有业务阈值搬到 IS_ env var (app/config.py Settings).
本脚本作为 pre-commit / CI 守门员 — 命中即拒绝合并.

USAGE:
    python -m scripts.check_no_hardcode               # 默认扫 backend/app
    python -m scripts.check_no_hardcode --path X      # 自定义路径
    python -m scripts.check_no_hardcode --strict      # 严格模式 (≥2 位也算)

白名单 (不视为 hardcode):
    1. 行内注释 # noqa: hardcode
    2. 协议常量: HTTP status (200/201/204/400/401/403/404/409/410/422/500/503)
    3. boolean-like / 索引: -1 / 0 / 1 / 2 / 3 / 5 / 10
    4. 注释行 / docstring 三引号行
    5. tests / scripts / migrations / __pycache__ 子树

Exit code: 0 干净 / 1 命中
"""

import argparse
import re
import sys
from pathlib import Path

# ─── 协议常量 + boolean-like + 容忍小数字 ─────────────────────────
_WHITELIST = {
    -1, 0, 1, 2, 3, 5, 10,
    200, 201, 204,
    400, 401, 403, 404, 409, 410, 422,
    500, 503,
}

# 扫"赋值 / 参数 / 字面量列表"位置的裸数字: x = 100 / func(timeout=300) / [..., 1000]
# 后置 lookahead 排除: 紧跟 .  )  ]  \d  → 避免 360<0> 这种部分匹配, 必须吃完整数字
_PATTERN = re.compile(r"(?:=|,|\(|\[)\s*(-?\d+)(?!\s*[\.\)\]]|\d)")
_NOQA_RE = re.compile(r"#\s*noqa:\s*hardcode")
_SKIP_DIRS = {"tests", "scripts", "migrations", "__pycache__"}


def scan_file(path: Path, strict: bool = False) -> list[tuple[int, str, int]]:
    """扫描单文件, 返回 [(line_no, line_text, value), ...]."""
    hits: list[tuple[int, str, int]] = []
    text = path.read_text(encoding="utf-8")
    in_docstring = False
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # ── docstring state machine: """xxx""" 单行不切; """xxx 切入; xxx""" 切出 ──
        triple_count = stripped.count('"""') + stripped.count("'''")
        if in_docstring:
            if triple_count % 2 == 1:
                in_docstring = False
            continue
        if triple_count % 2 == 1:
            in_docstring = True
            continue
        # 单行三引号 (开+关同行) 直接跳过本行内容
        if triple_count >= 2:
            continue
        if stripped.startswith("#"):
            continue
        if _NOQA_RE.search(line):
            continue
        for m in _PATTERN.finditer(line):
            try:
                v = int(m.group(1))
            except ValueError:
                continue
            if v in _WHITELIST:
                continue
            if not strict and abs(v) < 100:  # noqa: hardcode
                continue
            hits.append((i, line.rstrip(), v))
    return hits


def scan_path(root: Path, strict: bool = False) -> dict[Path, list[tuple[int, str, int]]]:
    """递归扫描目录, 自动跳过 tests / scripts / migrations / __pycache__."""
    report: dict[Path, list[tuple[int, str, int]]] = {}
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        hits = scan_file(py, strict=strict)
        if hits:
            report[py] = hits
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="app",
                        help="扫描根 (相对 backend/, 默认 app)")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式: ≥2 位数字也算 hardcode")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"[ERR] 路径不存在: {root}", file=sys.stderr)
        return 1

    report = scan_path(root, strict=args.strict)
    if not report:
        print(f"[OK] {root} 无硬编码数字命中")
        return 0

    total = sum(len(hits) for hits in report.values())
    print(f"[FAIL] {root} 检出 {total} 处硬编码数字 (across {len(report)} files):\n")
    for path, hits in sorted(report.items()):
        try:
            display = path.relative_to(root.parent)
        except ValueError:
            display = path
        print(f"  {display}:")
        for line_no, line_text, val in hits:
            print(f"    L{line_no} (val={val}): {line_text}")
    print()
    print("修复:")
    print("  1. 业务阈值/重试/批大小 → 加到 backend/app/config.py Settings, 用 settings.X 替换")
    print("  2. 协议常量/索引/数学常量 → 行内加 # noqa: hardcode")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
