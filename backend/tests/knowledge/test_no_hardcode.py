"""Stage 2 Task 8 — backend/app/ 必须 0 hardcode 命中."""
from pathlib import Path

import pytest

from scripts.check_no_hardcode import scan_path


def test_no_hardcode_in_backend_app() -> None:
    """扫描 backend/app/, 必须 0 命中.

    命中说明该数字应:
    1. 加到 backend/app/config.py Settings, 用 settings.X 替换 (业务阈值)
    2. 加 # noqa: hardcode 行内注释 (协议常量/索引/数学常量/日志格式)
    """
    root = Path(__file__).parent.parent.parent / "app"
    report = scan_path(root, strict=False)
    if not report:
        return  # OK
    msg_lines = ["backend/app/ 检出 hardcode:"]
    for path, hits in sorted(report.items()):
        for line_no, line_text, val in hits:
            msg_lines.append(f"  {path.name}:L{line_no} val={val}: {line_text}")
    msg_lines.append("修复: settings.X 或 # noqa: hardcode")
    pytest.fail("\n".join(msg_lines))
