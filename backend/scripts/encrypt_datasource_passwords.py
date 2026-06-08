#!/usr/bin/env python3
"""
encrypt_datasource_passwords.py — 存量 DataSource.password 明文 → Fernet 密文

背景: P0-2 引入 EncryptedString 后, 新写入的密码自动加密, 但升级前已落库的明文
行仍是明文 (靠 EncryptedString 的 InvalidToken fallback 才可读)。本脚本一次性把
存量明文重写为密文。

幂等: 直接读 raw 列值, 用 Fernet 试解密 —— 成功即已是密文, 跳过; 失败 (InvalidToken)
即明文, 加密后 UPDATE。可重复运行, 已加密行不会被二次加密。

安全: 默认 dry-run, 只统计不写库。须显式 --execute 才真正 UPDATE。

使用方式:
    cd backend
    python scripts/encrypt_datasource_passwords.py            # dry-run, 只统计
    python scripts/encrypt_datasource_passwords.py --execute  # 真正加密落库
"""

import asyncio
import sys
from pathlib import Path

# ── 项目根路径注入 ──────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import InvalidToken
from sqlalchemy import text

from app.db.crypto import _fernet
from app.db.metadata import engine


async def migrate(execute: bool = False) -> dict:
    """遍历 datasources, 把明文 password 重写为 Fernet 密文.

    返回统计: {total, encrypted, already_encrypted, empty}
    """
    stats = {"total": 0, "encrypted": 0, "already_encrypted": 0, "empty": 0}

    # raw 连接 — 必须绕开 ORM 的 EncryptedString 自动解密, 才能看到列里的真实存储值
    async with engine.begin() as conn:
        rows = (await conn.execute(
            text("SELECT id, password FROM datasources ORDER BY id")
        )).fetchall()
        stats["total"] = len(rows)
        print(f"[migrate] 共 {len(rows)} 条 datasource 待检查")

        for ds_id, raw_pw in rows:
            if not raw_pw:
                stats["empty"] += 1
                continue

            # 试解密 — 成功 = 已是密文, 跳过
            try:
                _fernet.decrypt(raw_pw.encode())
                stats["already_encrypted"] += 1
                continue
            except InvalidToken:
                pass  # 明文, 需加密

            cipher = _fernet.encrypt(raw_pw.encode()).decode()
            if execute:
                await conn.execute(
                    text("UPDATE datasources SET password = :pw WHERE id = :i"),
                    {"pw": cipher, "i": ds_id},
                )
                print(f"  [encrypted] ds_id={ds_id}")
            else:
                print(f"  [dry-run] ds_id={ds_id} 明文 → 将加密")
            stats["encrypted"] += 1

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="存量 DataSource 密码加密迁移")
    parser.add_argument(
        "--execute", action="store_true",
        help="真正写库 (默认 dry-run, 只统计不改动)",
    )
    args = parser.parse_args()

    stats = asyncio.run(migrate(execute=args.execute))

    print("\n" + "=" * 40)
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(
        f"[{mode}] total={stats['total']} encrypted={stats['encrypted']} "
        f"already_encrypted={stats['already_encrypted']} empty={stats['empty']}"
    )
    if not args.execute and stats["encrypted"]:
        print("提示: 加 --execute 才会真正加密落库")


if __name__ == "__main__":
    main()
