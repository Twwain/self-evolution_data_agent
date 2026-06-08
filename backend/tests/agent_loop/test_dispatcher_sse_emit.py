"""P0-3 Task 3: dispatcher 注入 sse_emit 基础设施测试.

验证 build_bound_registry(sse_emit=...) 能把 sse_emit 注入到接收此 kwarg 的 tool,
不接收的 tool 签名不受影响 (inspect.signature filter).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engine import agent_loop_dispatcher
from app.engine.tools import registry as registry_mod


@pytest.fixture
def fake_sse_emit():
    """fake sse_emit AsyncMock — 验调用记录."""
    return AsyncMock()


class TestDispatcherInjectsSseEmit:
    @pytest.mark.asyncio
    async def test_tool_with_sse_emit_kwarg_receives_injection(
        self, fake_sse_emit, monkeypatch,
    ):
        """工具签名声明 sse_emit kwarg → dispatcher 注入."""
        called_kwargs = {}

        async def fake_tool(*, namespace_id: int, sse_emit, foo: str):
            called_kwargs["namespace_id"] = namespace_id
            called_kwargs["sse_emit"] = sse_emit
            called_kwargs["foo"] = foo
            return {"ok": True}

        monkeypatch.setattr(registry_mod, "REGISTRY", {"fake_tool": fake_tool})

        bound = agent_loop_dispatcher.build_bound_registry(
            db=MagicMock(), namespace_id=99, ns_slug="ns_t",
            trace_id="trace-x", sse_emit=fake_sse_emit,
        )
        result = await bound["fake_tool"](foo="bar")

        assert result == {"ok": True}
        assert called_kwargs["namespace_id"] == 99
        assert called_kwargs["sse_emit"] is fake_sse_emit
        assert called_kwargs["foo"] == "bar"

    @pytest.mark.asyncio
    async def test_tool_without_sse_emit_kwarg_unaffected(
        self, fake_sse_emit, monkeypatch,
    ):
        """工具签名未声明 sse_emit → 不注入, 不抛 TypeError."""
        called_kwargs = {}

        async def fake_tool(*, namespace_id: int, foo: str):
            called_kwargs["namespace_id"] = namespace_id
            called_kwargs["foo"] = foo
            return {"ok": True}

        monkeypatch.setattr(registry_mod, "REGISTRY", {"fake_tool": fake_tool})

        bound = agent_loop_dispatcher.build_bound_registry(
            db=MagicMock(), namespace_id=99, ns_slug="ns_t",
            trace_id="trace-x", sse_emit=fake_sse_emit,
        )
        result = await bound["fake_tool"](foo="bar")

        assert result == {"ok": True}
        assert "sse_emit" not in called_kwargs
        assert called_kwargs["namespace_id"] == 99

    @pytest.mark.asyncio
    async def test_dispatcher_signature_includes_sse_emit(self):
        """build_bound_registry 签名应有 sse_emit 必填 kwarg."""
        import inspect
        sig = inspect.signature(agent_loop_dispatcher.build_bound_registry)
        assert "sse_emit" in sig.parameters, "build_bound_registry 需声明 sse_emit kwarg"
