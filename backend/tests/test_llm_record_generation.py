"""langfuse generation span input 瘦身测试.

覆盖 _last_input_block — agent_loop 多轮迭代时, 单次 generation span 只发本轮
"新增输入" 而非全量 messages, 防 OTLP payload 超限触发 5s 超时.
"""

from __future__ import annotations

from app.engine.llm import _last_input_block


def test_empty_messages():
    assert _last_input_block([]) == []


def test_single_user_only():
    msgs = [{"role": "user", "content": "你好"}]
    assert _last_input_block(msgs) == [{"role": "user", "content": "你好"}]


def test_system_plus_user_first_turn():
    msgs = [
        {"role": "system", "content": "你是 agent"},
        {"role": "user", "content": "查询订单"},
    ]
    out = _last_input_block(msgs)
    assert out == msgs


def test_drops_history_after_assistant():
    """关键场景: agent 多轮迭代时, 仅保留最后一个 assistant 之后的 tool_result + system."""
    msgs = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1 思考...", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "R1"},
        {"role": "assistant", "content": "A2 思考...", "tool_calls": [{"id": "t2"}]},
        {"role": "tool", "tool_call_id": "t2", "content": "R2 (本轮新增)"},
    ]
    out = _last_input_block(msgs)
    # 应只剩 system + 最后一个 tool result
    assert out == [
        {"role": "system", "content": "persona"},
        {"role": "tool", "tool_call_id": "t2", "content": "R2 (本轮新增)"},
    ]


def test_no_system_no_assistant():
    """退化场景: 没有 assistant 也没 system, 取最后一条."""
    msgs = [
        {"role": "user", "content": "Q1"},
        {"role": "user", "content": "Q2 (latest)"},
    ]
    out = _last_input_block(msgs)
    assert out == [{"role": "user", "content": "Q2 (latest)"}]


def test_payload_size_reduction():
    """量化验证: 12 轮 agent 迭代后, 新策略 payload < 旧策略 1/5."""
    big_tool_result = "X" * 10_000  # 10KB tool 输出
    msgs: list[dict] = [{"role": "system", "content": "persona"}]
    msgs.append({"role": "user", "content": "原始问题"})
    for i in range(12):
        msgs.append({"role": "assistant", "content": f"思考{i}", "tool_calls": [{"id": f"t{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": big_tool_result})

    full_size = sum(len(str(m.get("content", ""))) for m in msgs)
    block = _last_input_block(msgs)
    block_size = sum(len(str(m.get("content", ""))) for m in block)

    assert block_size < full_size / 5  # 至少 5x 瘦身
    assert len(block) == 2  # system + 最后一条 tool result
