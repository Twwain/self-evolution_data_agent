"""config.py Error_Class 设置 + 启动期不变量测试 (Property 30; task 11.2).

cd backend && python -m pytest tests/agent_loop/test_error_class_settings.py --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import pytest


def _make_settings(monkeypatch, **env):
    from app.config import Settings
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    return Settings()


def test_defaults():
    from app.config import settings
    assert settings.agent_loop_error_class_window_size == 5
    assert settings.agent_loop_error_class_threshold == 2
    assert settings.agent_loop_max_forced_clarify_per_class == 1
    assert settings.agent_loop_error_class_msg_signature_len == 80


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 30: threshold ≤ {window, dead_loop_window} 启动期强制
def test_property_30_threshold_gt_deadloop_raises(monkeypatch):
    with pytest.raises(ValueError, match="DEAD_LOOP_WINDOW"):
        _make_settings(
            monkeypatch,
            IS_AGENT_LOOP_ERROR_CLASS_THRESHOLD=5,
            IS_AGENT_LOOP_DEAD_LOOP_WINDOW=3,
            IS_AGENT_LOOP_ERROR_CLASS_WINDOW_SIZE=10,
        )


def test_property_30_threshold_gt_window_raises(monkeypatch):
    with pytest.raises(ValueError, match="WINDOW_SIZE"):
        _make_settings(
            monkeypatch,
            IS_AGENT_LOOP_ERROR_CLASS_THRESHOLD=8,
            IS_AGENT_LOOP_DEAD_LOOP_WINDOW=10,
            IS_AGENT_LOOP_ERROR_CLASS_WINDOW_SIZE=5,
        )


def test_property_30_both_satisfied_ok(monkeypatch):
    s = _make_settings(
        monkeypatch,
        IS_AGENT_LOOP_ERROR_CLASS_THRESHOLD=3,
        IS_AGENT_LOOP_DEAD_LOOP_WINDOW=3,
        IS_AGENT_LOOP_ERROR_CLASS_WINDOW_SIZE=5,
    )
    assert s.agent_loop_error_class_threshold == 3
