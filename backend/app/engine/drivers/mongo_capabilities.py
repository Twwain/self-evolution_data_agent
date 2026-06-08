"""MongoDB aggregation operator availability by server version.

纯函数 + 静态 JSON 表; driver 启动时调 buildInfo 拿 versionArray,
传入 compute_unsupported_ops 即可拿到当前 server 不支持的算子列表.

来源: MongoDB Manual 各算子页 'New in version X.Y' 标注 (2026-05 抽样).
新增 server 大版本时手工 review 一次即可.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_OP_TABLE_PATH = Path(__file__).parent / "mongo_op_versions.json"
_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


@lru_cache(maxsize=1)
def _load_op_table() -> dict[str, tuple[int, ...]]:
    """Load and parse op->min_version table once."""
    raw = json.loads(_OP_TABLE_PATH.read_text(encoding="utf-8"))
    return {op: parse_version(v) for op, v in raw.items()}


def parse_version(s: str) -> tuple[int, int, int]:
    """Parse server version string to (major, minor, patch). Unknown -> (0,0,0)."""
    if not s:
        return (0, 0, 0)
    m = _VERSION_RE.match(s)
    if not m:
        return (0, 0, 0)
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    return (major, minor, patch)


def compute_unsupported_ops(server_version: str) -> list[str]:
    """Return sorted list of aggregation operators NOT available on this server.

    Unknown / unparseable version -> empty list (do not false-positive block).
    """
    cur = parse_version(server_version)
    if cur == (0, 0, 0):
        return []
    table = _load_op_table()
    out = [op for op, min_v in table.items() if cur < min_v]
    return sorted(out)
