from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERCURY_", env_file=".env", extra="ignore")
    env: Literal["demo", "test", "staging", "production"] = "demo"
    database_url: str = "sqlite:///.data/mercury.db"
    data_dir: Path = Path(".data")
    auth_mode: Literal["local", "oidc"] = "local"
    jwt_secret: SecretStr = SecretStr("")
    demo_password: SecretStr = SecretStr("")
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_authorization_url: str = ""
    oidc_token_url: str = ""
    oidc_redirect_uri: str = ""
    session_encryption_keys: list[SecretStr] = []
    session_hours: int = 8
    session_idle_minutes: int = 30
    login_attempts_per_minute: int = 15
    scanner_mode: Literal["disabled", "clamav"] = "disabled"
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    scanner_timeout: int = 15
    worker_lease_seconds: int = 120
    run_timeout_seconds: int = 600
    waiting_timeout_hours: int = 48
    model_mode: Literal["deterministic_test", "real"] = "deterministic_test"
    business_mode: Literal["synthetic", "external"] = "synthetic"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    embedding_model: str = ""
    webhook_secret: SecretStr = SecretStr("")
    allowed_origins: list[str] = ["http://localhost:3040", "http://127.0.0.1:3040"]
    writes_enabled: bool = True
    max_model_calls: int = 16
    token_budget: int = 24000
    model_timeout: int = 25
    upload_limit: int = 5 * 1024 * 1024
    approval_ttl: int = 86400
    access_token_minutes: int = 30
    improvement_min_samples: int = 30
    overdue_hours: int = 24
    require_separation_of_duties: bool = True

    @model_validator(mode="after")
    def validate_environment(self):
        if self.auth_mode == "local" and len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError(
                "Local auth requires MERCURY_JWT_SECRET of at least 32 characters. Run configure."
            )
        if self.auth_mode == "oidc" and not all(
            (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url.startswith("https://"))
        ):
            raise ValueError("OIDC requires a fixed HTTPS JWKS URL, issuer and audience")
        if self.oidc_client_id:
            from cryptography.fernet import Fernet

            for endpoint in (
                self.oidc_issuer,
                self.oidc_authorization_url,
                self.oidc_token_url,
                self.oidc_redirect_uri,
            ):
                parsed = urlparse(endpoint)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.fragment:
                    raise ValueError("Browser OIDC requires fixed HTTPS endpoints without credentials")
            if not self.session_encryption_keys:
                raise ValueError("Browser OIDC requires an encryption key for stored provider tokens")
            for key in self.session_encryption_keys:
                Fernet(key.get_secret_value().encode())
        if (
            min(
                self.session_hours,
                self.session_idle_minutes,
                self.worker_lease_seconds,
                self.run_timeout_seconds,
                self.waiting_timeout_hours,
                self.login_attempts_per_minute,
            )
            <= 0
        ):
            raise ValueError("Timeouts, budgets and rate limits must be positive")
        if self.model_mode == "real" and not (self.llm_model and self.llm_api_key.get_secret_value()):
            raise ValueError("Real model mode requires a model and API key; no test fallback exists")
        endpoint = urlparse(self.llm_base_url)
        if endpoint.scheme != "https" or endpoint.username or endpoint.password:
            raise ValueError("Model endpoint must use HTTPS without embedded credentials")
        if self.env in {"staging", "production"}:
            if self.auth_mode != "oidc" or self.model_mode != "real" or self.business_mode != "external":
                raise ValueError(
                    "Staging/production forbid local identity, test models and synthetic business"
                )
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Staging/production require PostgreSQL")
            if "*" in self.allowed_origins:
                raise ValueError("Wildcard CORS is forbidden")
            if not self.require_separation_of_duties:
                raise ValueError("Staging/production require separate approval and execution identities")
        return self
