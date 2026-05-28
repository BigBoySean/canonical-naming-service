from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven configuration. Loads from process env and `.env` (if present).

    No hard-coded secrets. `ANTHROPIC_API_KEY` is only required when
    `LLM_ENABLED=true`; tests run offline with the LLM tier mocked.
    """

    anthropic_api_key: str | None = None
    llm_enabled: bool = False
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_timeout_seconds: int = 10
    fuzzy_threshold: int = 92

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — load env once per process."""
    return Settings()
