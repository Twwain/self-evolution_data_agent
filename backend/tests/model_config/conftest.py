"""model_config 测试专用 fixture."""
import pytest
import pytest_asyncio

from app.engine.model_registry import registry as _registry
from app.models.model_config import ModelConfig


@pytest_asyncio.fixture
async def seed_active_chat_model_config(db):
    row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-chat", model_type="CHAT",
        is_active=True, is_deleted=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    yield row
    row.is_deleted = True
    row.is_active = False
    await db.commit()


@pytest_asyncio.fixture
async def seed_active_embedding_model_config(db):
    row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-embedding", model_type="EMBEDDING",
        is_active=True, is_deleted=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    yield row
    row.is_deleted = True
    row.is_active = False
    await db.commit()


@pytest_asyncio.fixture
async def seed_active_model_configs(
    seed_active_chat_model_config,
    seed_active_embedding_model_config,
):
    return {"chat": seed_active_chat_model_config, "embedding": seed_active_embedding_model_config}


@pytest.fixture
def model_registry_isolated():
    # refresh_chat/refresh_embedding 内部已包含 _chat_client/_embedding_client = None
    _registry.refresh_chat(None)
    _registry.refresh_embedding(None)
    yield _registry
    _registry.refresh_chat(None)
    _registry.refresh_embedding(None)
