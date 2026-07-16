from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "劳动争议法律咨询助手"
    database_url: str = "sqlite:///./legal_advisor.db"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 30
    model_provider: str = "deterministic"
    model_redaction_enabled: bool = True
    model_consent_required: bool = True
    pseudonym_hmac_secret: str | None = None
    observability_hmac_secret: str | None = None
    model_api_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_timeout_seconds: float = 45.0
    deepseek_max_retries: int = 2
    embedding_provider: str = "deterministic"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str = "legal-hash-v1"
    embedding_dimensions: int = 1536
    embedding_timeout_seconds: float = 30.0
    embedding_batch_size: int = 8
    embedding_consent_provider: str = "embedding"
    embedding_consent_required: bool = True
    embedding_query_instruction: str = (
        "Given a Chinese labor dispute query, retrieve authoritative legal provisions "
        "that answer the query"
    )
    cors_origins: str = "http://localhost:3001,http://127.0.0.1:3001"
    auth_enabled: bool = False
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_signing_key: str | None = None
    oidc_algorithms: str = "RS256"
    oidc_tenant_claim: str = "tenant_id"
    oidc_roles_claim: str = "roles"

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        origins: list[str] = []
        configured_origins = self.cors_origins.split(",")
        if not self.auth_enabled:
            configured_origins.extend(
                ["http://localhost:3001", "http://127.0.0.1:3001"]
            )
        for configured_origin in configured_origins:
            origin = configured_origin.strip().rstrip("/")
            if not origin:
                continue
            origins.append(origin)
            parsed = urlsplit(origin)
            if parsed.hostname not in {"localhost", "127.0.0.1"}:
                continue
            alternate_host = "127.0.0.1" if parsed.hostname == "localhost" else "localhost"
            alternate_netloc = alternate_host
            if parsed.port is not None:
                alternate_netloc = f"{alternate_host}:{parsed.port}"
            origins.append(
                urlunsplit(
                    (parsed.scheme, alternate_netloc, parsed.path, parsed.query, parsed.fragment)
                )
            )
        return list(dict.fromkeys(origins))


@lru_cache
def get_settings() -> Settings:
    return Settings()
