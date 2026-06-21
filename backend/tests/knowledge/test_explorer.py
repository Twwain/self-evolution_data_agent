"""Unit tests for Phase A Explorer V3 (tool-assisted bounded loop).

全部 mock chat_completion_with_tools 返回 ToolUseResponse 对象.
list_dir / read_file / grep / find_files 仅在有工具调用路径的测试中 mock.

fixture 使用通用电商命名 (Order / Customer / Product) — 不含任何客户领域词汇.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.llm import ToolCall, ToolUseResponse
from app.knowledge.skeleton._base import ExplorerResult, Module, Skeleton

# ─────────────────────────────────────────────────────────────────────────────
# 公共 helper
# ─────────────────────────────────────────────────────────────────────────────

def _make_text_response(text: str) -> ToolUseResponse:
    """LLM 直接输出 JSON (无工具调用)."""
    return ToolUseResponse(text=text, tool_calls=[], stop_reason="end_turn")


def _make_tool_response(tool_calls: list[ToolCall]) -> ToolUseResponse:
    """LLM 发起工具调用."""
    return ToolUseResponse(text="", tool_calls=tool_calls, stop_reason="tool_use")


# ─────────────────────────────────────────────────────────────────────────────
# 公共 fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def ecommerce_skeleton() -> Skeleton:
    """小型电商骨架: 含实体类、服务类、DTO 类."""
    return Skeleton(
        modules=[
            Module(
                name="com/shop/order",
                files=[
                    "src/main/java/com/shop/order/Order.java",
                    "src/main/java/com/shop/order/OrderService.java",
                ],
                classes=["Order", "OrderService"],
            ),
            Module(
                name="com/shop/customer",
                files=["src/main/java/com/shop/customer/Customer.java"],
                classes=["Customer"],
            ),
            Module(
                name="com/shop/product",
                files=["src/main/java/com/shop/product/Product.java"],
                classes=["Product"],
            ),
        ],
        class_index={
            "Order": "src/main/java/com/shop/order/Order.java",
            "OrderService": "src/main/java/com/shop/order/OrderService.java",
            "Customer": "src/main/java/com/shop/customer/Customer.java",
            "Product": "src/main/java/com/shop/product/Product.java",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 基本路径测试: LLM 直接在文本中输出 JSON (无工具调用)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explorer_identifies_java_entities(ecommerce_skeleton):
    """正常路径: LLM 直接输出 JSON, Explorer 正确识别实体."""
    response = json.dumps({
        "focus_files": ["src/main/java/com/shop/order/Order.java"],
        "focus_classes": ["Order"],
        "reasoning": "Order is in entity path and has persistence naming conventions.",
    })
    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock:
        mock.return_value = _make_text_response(response)
        from app.knowledge.skeleton.explorer import explore_repo
        result: ExplorerResult = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "ok"
    assert "src/main/java/com/shop/order/Order.java" in result.focus_files
    assert "Order" in result.focus_classes
    assert result.reasoning != ""


@pytest.mark.asyncio
async def test_explorer_identifies_xml_mappers(ecommerce_skeleton):
    """Explorer 应支持非 Java 持久化支撑文件 (XML mapper 等)."""
    response = json.dumps({
        "focus_files": [
            "src/main/java/com/shop/order/Order.java",
            "src/main/resources/mapper/OrderMapper.xml",
        ],
        "focus_classes": ["Order"],
        "reasoning": "Order entity and its MyBatis mapper XML both require extraction.",
    })
    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock:
        mock.return_value = _make_text_response(response)
        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert "src/main/resources/mapper/OrderMapper.xml" in result.focus_files
    assert result.status == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 降级路径测试
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explorer_graceful_degradation_on_llm_failure(ecommerce_skeleton):
    """LLM 抛异常 → status=partial, focus_files=[], 不向上传播."""
    mock_fn = AsyncMock(side_effect=Exception("boom"))
    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               mock_fn):
        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "partial"
    assert result.focus_files == []


@pytest.mark.asyncio
async def test_explorer_bad_json_missing_required_fields(ecommerce_skeleton):
    """JSON 合法但缺 focus_classes → status=partial (不满足全三项非空条件)."""
    response = json.dumps({
        "focus_files": ["src/main/java/com/shop/order/Order.java"],
        # 故意省略 focus_classes
        "reasoning": "Found order entity file.",
    })
    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock:
        mock.return_value = _make_text_response(response)
        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "partial"


@pytest.mark.asyncio
async def test_explorer_partial_with_data_continues(ecommerce_skeleton):
    """focus_files 非空但 reasoning 为空 → status=partial, focus_files 仍保留数据.

    编排器可选择继续处理 partial 结果.
    """
    response = json.dumps({
        "focus_files": [
            "src/main/java/com/shop/order/Order.java",
            "src/main/java/com/shop/customer/Customer.java",
        ],
        "focus_classes": ["Order", "Customer"],
        "reasoning": "",  # 空 reasoning → partial
    })
    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock:
        mock.return_value = _make_text_response(response)
        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "partial"
    assert len(result.focus_files) > 0, "partial 结果仍应携带数据供编排器使用"


@pytest.mark.asyncio
async def test_explorer_partial_empty_falls_back(ecommerce_skeleton):
    """LLM 返回空 focus_files → status=partial, focus_files==[] (编排器回退路径)."""
    response = json.dumps({
        "focus_files": [],
        "focus_classes": [],
        "reasoning": "No persistence entities detected in this repository.",
    })
    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock:
        mock.return_value = _make_text_response(response)
        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "partial"
    assert result.focus_files == []


@pytest.mark.asyncio
async def test_explorer_falls_back_on_empty_llm_text(ecommerce_skeleton):
    """LLM 返回空文本 (response.text='' 且无 tool_calls) → partial."""
    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock:
        mock.return_value = _make_text_response("")
        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "partial"
    assert result.focus_files == []


# ─────────────────────────────────────────────────────────────────────────────
# 工具调用路径测试
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explorer_uses_tools_then_outputs_json(ecommerce_skeleton):
    """工具路径: LLM 先调 list_dir, 后输出 JSON. mock 两部分.

    此测试验证:
    1. chat_completion_with_tools 被调用至少两轮 (tools → JSON)
    2. 最终输出正确解析
    """
    final_json = json.dumps({
        "focus_files": ["src/main/java/com/shop/order/Order.java"],
        "focus_classes": ["Order"],
        "reasoning": "Explored src dirs, read entity package, found Order entity.",
    })

    tool_call_1 = ToolCall(id="tc1", name="list_dir", input={"path": "."})
    tool_call_2 = ToolCall(id="tc2", name="read_file",
                           input={"path": "src/main/java/com/shop/order/Order.java"})

    # mock list_dir 工具返回值 — path 在 lambda default 之外, 须用万能 mock
    mock_list_dir = MagicMock(return_value={
        "status": "ok",
        "dirs": ["src", "pom.xml"],
        "files": [],
    })
    mock_read_file = MagicMock(return_value={
        "status": "ok",
        "content": "public class Order { ... }",
        "total_lines": 10,
        "start_line": 0,
        "end_line": 10,
    })

    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock_llm, \
         patch("app.knowledge.skeleton.explorer.list_dir", mock_list_dir), \
         patch("app.knowledge.skeleton.explorer.read_file", mock_read_file):

        mock_llm.side_effect = [
            _make_tool_response([tool_call_1, tool_call_2]),
            _make_text_response(final_json),
        ]

        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "ok"
    assert "src/main/java/com/shop/order/Order.java" in result.focus_files
    assert "Order" in result.focus_classes


@pytest.mark.asyncio
async def test_explorer_dead_loop_terminates(ecommerce_skeleton):
    """连续 4 轮同 list_dir(".") → dead_loop 终止 → partial."""
    tool_call = ToolCall(id="tc_dl", name="list_dir", input={"path": "."})

    mock_list_dir = MagicMock(return_value={
        "status": "ok", "dirs": ["src", "pom.xml"], "files": [],
    })

    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock_llm, \
         patch("app.knowledge.skeleton.explorer.list_dir", mock_list_dir):

        # 返回 4 轮同 list_dir
        mock_llm.side_effect = [
            _make_tool_response([tool_call]),
            _make_tool_response([tool_call]),
            _make_tool_response([tool_call]),
            _make_tool_response([tool_call]),
        ]

        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "partial"
    assert result.reasoning == "dead_loop"
    assert result.focus_files == []


@pytest.mark.asyncio
async def test_explorer_unknown_tool_handled_gracefully(ecommerce_skeleton):
    """LLM 调未知工具 → 错误反馈, 不崩溃."""
    tool_call = ToolCall(id="tc_bad", name="nonexistent_tool", input={})

    final_json = json.dumps({
        "focus_files": ["src/main/java/com/shop/order/Order.java"],
        "focus_classes": ["Order"],
        "reasoning": "Found Order entity.",
    })

    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [
            _make_tool_response([tool_call]),
            _make_text_response(final_json),
        ]

        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop", skeleton=ecommerce_skeleton,
        )

    assert result.status == "ok"
    assert "Order" in result.focus_classes


# ─────────────────────────────────────────────────────────────────────────────
# V3 降级路径测试: 空 skeleton index / 迭代上限 / 非预置语言
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explorer_handles_empty_skeleton_index():
    """V3: 空 skeleton index (class_index={}) 时 Explorer 使用工具发现实体文件."""
    final_json = json.dumps({
        "focus_files": ["src/Order.java", "src/Customer.java"],
        "focus_classes": ["Order", "Customer"],
        "reasoning": "discovered via tool exploration",
    })

    tool_call_1 = ToolCall(id="tc1", name="list_dir", input={"path": "."})
    tool_call_2 = ToolCall(id="tc2", name="find_files", input={"glob": "*.java"})

    mock_list_dir = MagicMock(return_value={
        "status": "ok", "dirs": ["src"], "files": [],
    })
    mock_find_files = MagicMock(return_value={
        "status": "ok", "files": ["src/Order.java", "src/Customer.java"],
    })

    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock_llm, \
         patch("app.knowledge.skeleton.explorer.list_dir", mock_list_dir), \
         patch("app.knowledge.skeleton.explorer.find_files", mock_find_files):

        mock_llm.side_effect = [
            _make_tool_response([tool_call_1]),
            _make_tool_response([tool_call_2]),
            _make_text_response(final_json),
        ]

        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop",
            skeleton=Skeleton(class_index={}, modules=[]),
        )

    assert result.status == "ok"
    assert "src/Order.java" in result.focus_files
    assert "src/Customer.java" in result.focus_files
    assert "Order" in result.focus_classes


@pytest.mark.asyncio
async def test_explorer_dead_loop_terminates():
    """dead_loop: LLM 4 轮重复同 tool+input → 直接返回 partial (无迭代上限)."""
    final_json = json.dumps({
        "focus_files": ["src/Order.java"],
        "focus_classes": ["Order"],
        "reasoning": "stuck in dead loop",
    })

    # 4 轮完全相同的工具调用 — 触发 dead_loop 检测
    duplicate_tc = ToolCall(id="tc1", name="list_dir", input={"path": "src"})

    mock_list_dir = MagicMock(return_value={
        "status": "ok", "dirs": ["main"], "files": [],
    })

    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock_llm, \
         patch("app.knowledge.skeleton.explorer.list_dir", mock_list_dir):

        # 4 轮相同 tool_call → dead_loop
        mock_llm.side_effect = [
            _make_tool_response([duplicate_tc]),
            _make_tool_response([duplicate_tc]),
            _make_tool_response([duplicate_tc]),
            _make_tool_response([duplicate_tc]),
        ]

        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/shop",
            skeleton=Skeleton(class_index={}, modules=[]),
        )

    assert result.status == "partial"
    assert result.reasoning == "dead_loop"


@pytest.mark.asyncio
async def test_explorer_non_preset_language_discovers_via_grep():
    """非预置语言 (Swift): 空 skeleton 时 Explorer 用 find_files 发现 .swift 文件."""
    final_json = json.dumps({
        "focus_files": [
            "Sources/MyApp/Models/User.swift",
            "Sources/MyApp/Models/Product.swift",
        ],
        "focus_classes": ["User", "Product"],
        "reasoning": "discovered Swift entity files in Models directory",
    })

    tool_call = ToolCall(id="tc_swift", name="find_files", input={"glob": "*.swift"})

    mock_find_files = MagicMock(return_value={
        "status": "ok",
        "files": [
            "Sources/MyApp/Models/User.swift",
            "Sources/MyApp/Models/Product.swift",
        ],
    })

    with patch("app.knowledge.skeleton.explorer.chat_completion_with_tools",
               new_callable=AsyncMock) as mock_llm, \
         patch("app.knowledge.skeleton.explorer.find_files", mock_find_files):

        mock_llm.side_effect = [
            _make_tool_response([tool_call]),
            _make_text_response(final_json),
        ]

        from app.knowledge.skeleton.explorer import explore_repo
        result = await explore_repo(
            repo_path="/tmp/swift-app",
            skeleton=Skeleton(class_index={}),
        )

    assert result.status == "ok"
    assert any(p.endswith(".swift") for p in result.focus_files)
