from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://knowledge_app:unconfigured@localhost:5432/knowledge_os"
    )
    migration_database_url: SecretStr | None = None
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_session_token: SecretStr = SecretStr("")
    s3_bucket: str = "knowledge-os"
    s3_region: str = "us-east-1"
    system_api_token: SecretStr = SecretStr("")
    download_signing_key: SecretStr = SecretStr("")
    cors_origins: list[str] = ["http://localhost:3000"]
    auth_register_limit: int = Field(default=5, ge=1, le=100)
    credential_keys: SecretStr = SecretStr("")
    credential_active_key: str = "v1"
    # Operator allowlist only. Never accept a browser-supplied model endpoint URL.
    ollama_endpoints: dict[str, str] = {}
    local_embedding_endpoint: str = ""
    # Explicit operator opt-in; a saved credential alone never enables paid traffic.
    openai_enabled: bool = False
    deepseek_enabled: bool = False
    collector_enabled: bool = False
    registration_enabled: bool = True
    email_verification_required: bool = False
    public_app_url: str = "http://localhost:3000"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_security: Literal["starttls", "tls", "plain"] = "starttls"
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver", "api"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
