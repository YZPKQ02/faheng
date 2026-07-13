from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "劳动争议法律咨询助手"
    database_url: str = "sqlite:///./legal_advisor.db"
    model_provider: str = "deterministic"
    model_api_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_timeout_seconds: float = 45.0
    deepseek_max_retries: int = 2
    cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
