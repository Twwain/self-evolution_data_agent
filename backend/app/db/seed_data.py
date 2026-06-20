"""种子数据 — 幂等 INSERT (ON CONFLICT DO NOTHING)."""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

# 注: namespace_id 为 NULL 时, UNIQUE(name, namespace_id) 约束不阻止重复行
# (PostgreSQL NULL 互不相等), ON CONFLICT 也不会触发 → 改用 WHERE NOT EXISTS 保证幂等.
_SEED_SQL = """
INSERT INTO extractor_profiles (name, display_name, description, languages, hint_text, is_builtin, is_enabled)
SELECT
    'java-spring',
    'Java Spring (JPA + MyBatis + MongoDB)',
    '适用于 Spring Boot 项目, 使用 JPA/Hibernate、MyBatis 或 Spring Data MongoDB',
    '["Java", "Kotlin"]'::jsonb,
    '实体类通常标注 @Entity/@Table(JPA) 或 @Document(MongoDB)。关联关系标注 @ManyToOne/@OneToMany/@DBRef。MyBatis 映射在 *Mapper.xml 中。枚举可能在独立的 enum 类中定义。',
    true, true
WHERE NOT EXISTS (
    SELECT 1 FROM extractor_profiles WHERE name = 'java-spring' AND namespace_id IS NULL
);
"""


async def ensure_extractor_profile_seeds(engine: AsyncEngine) -> None:
    """幂等插入内置 java-spring profile 种子数据."""
    async with engine.connect() as conn:
        await conn.execute(text(_SEED_SQL))
        await conn.commit()
    log.info("[seed_data] extractor_profiles java-spring seed ensured")
