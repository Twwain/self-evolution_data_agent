from app.config import settings


def test_agent_keepalive_default():
    assert hasattr(settings, "agent_keepalive_interval_secs")
    assert settings.agent_keepalive_interval_secs == 30


def test_qmql_config_defaults():
    assert settings.qmql_extract_interval_hours == 24
    assert settings.qmql_extract_min_success_age_hours == 1
    assert settings.qmql_extract_max_per_run == 50
