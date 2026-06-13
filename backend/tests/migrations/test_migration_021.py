"""migration_021: role 扩列 + namespace.created_by + admin 升级 + 访问权回填 + 自引用 FK 修复。

PostgreSQL 真实库, 不 mock。用独立 engine + 已提交连接 (抄 test_migration_008),
因迁移 DDL/DML 需真实提交可见, 与 conftest 的 rollback `db` fixture 冲突 (savepoint
回滚 + 独立连接看不到种子数据)。
"""
import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema_migrations import _migrate_rbac_three_tier

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


@pytest_asyncio.fixture
async def mig_engine():
    """独立 engine + 最小骨架表 (模拟存量旧库: role VARCHAR(10), 自引用 FK 无 ondelete)。"""
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS user_namespace_access CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS namespaces CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        # 旧库形态: role VARCHAR(10), created_by 自引用 FK 无 ondelete (NO ACTION)
        await conn.execute(text("""
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE,
                password_hash VARCHAR(128) DEFAULT 'x',
                role VARCHAR(10) DEFAULT 'user',
                is_active BOOLEAN DEFAULT TRUE,
                created_by INTEGER REFERENCES users(id)
            )
        """))
        await conn.execute(text("""
            CREATE TABLE namespaces (
                id SERIAL PRIMARY KEY, name VARCHAR(100), slug VARCHAR(100) UNIQUE,
                description TEXT DEFAULT ''
            )
        """))
        await conn.execute(text("""
            CREATE TABLE user_namespace_access (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                namespace_id INTEGER REFERENCES namespaces(id) ON DELETE CASCADE,
                CONSTRAINT uq_user_ns UNIQUE (user_id, namespace_id)
            )
        """))
    yield eng
    # ★ teardown 必须恢复 schema: 本 fixture 在真实测试库上 DROP 了核心表 (users/
    #   namespaces/user_namespace_access)。若不恢复, 后续在同库收集的测试 (如
    #   tests/models/) 全量跑 pytest 时会因表缺失崩溃。prepare_test_schema 重建全部表。
    from tests._db_schema_sync import prepare_test_schema
    await prepare_test_schema(eng)
    await eng.dispose()


@pytest.mark.asyncio
async def test_backfill_preserves_existing_admin_access(mig_engine):
    """核心: 升级前的全局 admin, 升级后经回填仍能访问所有现存 namespace。"""
    async with mig_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO users (username, role) VALUES ('admin','admin'),('biz_admin','admin')"
        ))
        await conn.execute(text(
            "INSERT INTO namespaces (name, slug) VALUES ('Sales','sales-121'),('Ops','ops-121')"
        ))

    await _migrate_rbac_three_tier(mig_engine)

    async with mig_engine.connect() as conn:
        boot_role = await conn.scalar(text("SELECT role FROM users WHERE username='admin'"))
        assert boot_role == "super_admin"
        biz_role = await conn.scalar(text("SELECT role FROM users WHERE username='biz_admin'"))
        assert biz_role == "admin"
        # ★ 业务 admin 获得对全部 2 个现存 ns 的回填访问
        cnt = await conn.scalar(text(
            "SELECT count(*) FROM user_namespace_access una "
            "JOIN users u ON u.id=una.user_id WHERE u.username='biz_admin'"
        ))
        assert cnt == 2
        # role 列已扩宽 (能存 super_admin = 11 字符)
        width = await conn.scalar(text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='role'"
        ))
        assert width == 20


@pytest.mark.asyncio
async def test_self_ref_fk_set_null_after_migration(mig_engine):
    """第 5 步: 自引用 FK 修为 SET NULL → 删有下属的 admin, 子用户 created_by 置 NULL。"""
    async with mig_engine.begin() as conn:
        await conn.execute(text("INSERT INTO users (username, role) VALUES ('parent','admin')"))
        pid = await conn.scalar(text("SELECT id FROM users WHERE username='parent'"))
        await conn.execute(text(
            "INSERT INTO users (username, role, created_by) VALUES ('child','user',:p)"
        ), {"p": pid})

    await _migrate_rbac_three_tier(mig_engine)

    async with mig_engine.begin() as conn:
        pid = await conn.scalar(text("SELECT id FROM users WHERE username='parent'"))
        # 删父 → 不抛 IntegrityError (SET NULL 生效)
        await conn.execute(text("DELETE FROM users WHERE id=:p"), {"p": pid})
        child_cb = await conn.scalar(text("SELECT created_by FROM users WHERE username='child'"))
        assert child_cb is None


@pytest.mark.asyncio
async def test_idempotent_rerun(mig_engine):
    await _migrate_rbac_three_tier(mig_engine)
    await _migrate_rbac_three_tier(mig_engine)  # 二次不报错 (回填 width guard 跳过, FK 已 SET NULL)


@pytest.mark.asyncio
async def test_backfill_only_runs_once_not_for_post_migration_admins(mig_engine):
    """★ 回填是一次性的: 迁移后新建的 admin 不应在 rerun 时被回填到全部 ns。
    防"每次 run_all 重跑回填 → admin 作用域被永久架空"的设计缺陷。"""
    async with mig_engine.begin() as conn:
        await conn.execute(text("INSERT INTO users (username, role) VALUES ('admin','admin')"))
        await conn.execute(text("INSERT INTO namespaces (name, slug) VALUES ('Old','old-121b')"))

    # 首次迁移 (width=10 → 回填 + 列扩宽)
    await _migrate_rbac_three_tier(mig_engine)

    # 迁移后新建一个 admin + 一个 namespace (模拟运行期新增)
    async with mig_engine.begin() as conn:
        await conn.execute(text("INSERT INTO users (username, role) VALUES ('new_admin','admin')"))
        await conn.execute(text("INSERT INTO namespaces (name, slug) VALUES ('New','new-121b')"))

    # rerun (width 已是 20 → 回填必须跳过)
    await _migrate_rbac_three_tier(mig_engine)

    async with mig_engine.connect() as conn:
        cnt = await conn.scalar(text(
            "SELECT count(*) FROM user_namespace_access una "
            "JOIN users u ON u.id=una.user_id WHERE u.username='new_admin'"
        ))
        assert cnt == 0  # 新 admin 未被回填, 作用域语义保持
