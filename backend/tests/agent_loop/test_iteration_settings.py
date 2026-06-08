"""Stage 4 → 2026-05-12 reform: 单一 max_iterations → 3 配额 + 开关."""
import pytest


def test_new_settings_exist_with_correct_defaults():
    from app.config import settings
    assert settings.agent_loop_iteration_limit_enabled is True
    assert settings.agent_loop_max_exploratory_calls == 25
    assert settings.agent_loop_max_decisive_calls == 15
    assert settings.agent_loop_max_total_iterations == 40


def test_old_max_iterations_field_removed():
    from app.config import Settings
    fields = set(Settings.model_fields.keys())
    assert "agent_loop_max_iterations" not in fields, (
        "agent_loop_max_iterations 字段应删除 — 改用 3 配额"
    )


def test_env_vars_override(monkeypatch):
    monkeypatch.setenv("IS_AGENT_LOOP_ITERATION_LIMIT_ENABLED", "false")
    monkeypatch.setenv("IS_AGENT_LOOP_MAX_EXPLORATORY_CALLS", "50")
    monkeypatch.setenv("IS_AGENT_LOOP_MAX_DECISIVE_CALLS", "20")
    monkeypatch.setenv("IS_AGENT_LOOP_MAX_TOTAL_ITERATIONS", "80")
    from app.config import Settings
    s = Settings()
    assert s.agent_loop_iteration_limit_enabled is False
    assert s.agent_loop_max_exploratory_calls == 50
    assert s.agent_loop_max_decisive_calls == 20
    assert s.agent_loop_max_total_iterations == 80
