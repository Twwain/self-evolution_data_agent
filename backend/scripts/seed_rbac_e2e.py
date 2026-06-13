#!/usr/bin/env python3
"""seed_rbac_e2e.py — 三角色 RBAC e2e 专用种子 (真实数据).

职责:
  1. 触发 schema_migrations.run_all (含 migration_021: 原 admin→super_admin + 回填)
  2. 确保 admin 账户存在且 role=super_admin, 密码 reset 为 IS_DEFAULT_ADMIN_PASSWORD
  3. 建测试账号矩阵 (密码均 admin123456):
     - e2e-rbac-admin-a (admin, 创建 e2e-rbac-ns-alpha + e2e-rbac-user-x)
     - e2e-rbac-admin-b (admin, 创建 e2e-rbac-ns-beta)
     - e2e-rbac-user-x  (user, 被授 e2e-rbac-ns-alpha)
  4. 输出 JSON (账号/ns id) 给 e2e 消费

幂等: 重复运行不重复建 (按 username/slug upsert), 安全 (仅碰 e2e-rbac-* 前缀)。

用法:
  cd backend && python scripts/seed_rbac_e2e.py
  cd backend && python scripts/seed_rbac_e2e.py --json
  cd backend && python scripts/seed_rbac_e2e.py --cleanup
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.auth import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_USER, hash_password
from app.config import settings
from app.db.metadata import async_session, engine
from app.db.schema_migrations import run_all as run_schema_migrations
from app.models.namespace import Namespace
from app.models.user import User, UserNamespaceAccess

PWD = "admin123456"  # 与 IS_DEFAULT_ADMIN_PASSWORD 默认一致
PREFIX = "e2e-rbac-"


async def _ensure_user(db, username, role, password, created_by=None):
    u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if u is None:
        u = User(
            username=username, password_hash=hash_password(password),
            role=role, created_by=created_by,
        )
        db.add(u)
    else:
        u.role = role
        u.password_hash = hash_password(password)
        u.created_by = created_by
    await db.flush()
    return u


async def _ensure_ns(db, name, slug, created_by):
    ns = (await db.execute(select(Namespace).where(Namespace.slug == slug))).scalar_one_or_none()
    if ns is None:
        ns = Namespace(name=name, slug=slug, description="e2e rbac", created_by=created_by)
        db.add(ns)
    else:
        ns.created_by = created_by
    await db.flush()
    return ns


async def _ensure_access(db, user_id, ns_id):
    exists = (await db.execute(select(UserNamespaceAccess).where(
        UserNamespaceAccess.user_id == user_id, UserNamespaceAccess.namespace_id == ns_id
    ))).scalar_one_or_none()
    if exists is None:
        db.add(UserNamespaceAccess(user_id=user_id, namespace_id=ns_id))
    await db.flush()


async def seed():
    # 1. 迁移 (含 migration_021)
    await run_schema_migrations(engine)
    async with async_session() as db:
        # 2. admin → super_admin + 密码 reset
        await _ensure_user(db, "admin", ROLE_SUPER_ADMIN, settings.default_admin_password)
        # 3. 账号矩阵
        admin_a = await _ensure_user(db, f"{PREFIX}admin-a", ROLE_ADMIN, PWD)
        admin_b = await _ensure_user(db, f"{PREFIX}admin-b", ROLE_ADMIN, PWD)
        ns_alpha = await _ensure_ns(db, "E2E Alpha", f"{PREFIX}ns-alpha", admin_a.id)
        ns_beta = await _ensure_ns(db, "E2E Beta", f"{PREFIX}ns-beta", admin_b.id)
        await _ensure_access(db, admin_a.id, ns_alpha.id)
        await _ensure_access(db, admin_b.id, ns_beta.id)
        user_x = await _ensure_user(db, f"{PREFIX}user-x", ROLE_USER, PWD, created_by=admin_a.id)
        await _ensure_access(db, user_x.id, ns_alpha.id)
        await db.commit()
        return {
            "admin": "admin", "admin_a": admin_a.username, "admin_b": admin_b.username,
            "user_x": user_x.username, "ns_alpha": ns_alpha.id, "ns_beta": ns_beta.id,
            "password": PWD,
        }


async def cleanup():
    async with async_session() as db:
        await db.execute(delete(User).where(User.username.like(f"{PREFIX}%")))
        await db.execute(delete(Namespace).where(Namespace.slug.like(f"{PREFIX}%")))
        await db.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    if args.cleanup:
        asyncio.run(cleanup())
        return
    result = asyncio.run(seed())
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[seed_rbac_e2e] done: {result}")


if __name__ == "__main__":
    main()
