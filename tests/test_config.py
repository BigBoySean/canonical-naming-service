import pytest

from canonical_naming.config import Settings, get_settings

_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "LLM_ENABLED",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "FUZZY_THRESHOLD",
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_defaults_load_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Bypass any developer `.env` so the test is deterministic.
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key is None
    assert settings.llm_enabled is False
    assert settings.fuzzy_threshold == 92
    assert settings.llm_model == "claude-haiku-4-5-20251001"
    assert settings.llm_timeout_seconds == 10


def test_env_override_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FUZZY_THRESHOLD", "80")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.fuzzy_threshold == 80
