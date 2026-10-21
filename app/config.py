from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg://warehouse:warehouse@localhost:5433/warehouse"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    ENVIRONMENT: Literal["local", "prod"] = "local"
    DATABASE_URL: str = DEFAULT_DATABASE_URL

    WEB_URL: str = "http://localhost:3000"
    REVALIDATE_SECRET: str = ""
    REVALIDATE_TIMEOUT_SECONDS: float = 5.0

    COGNITO_REGION: str = "sa-east-1"
    COGNITO_USER_POOL_ID: str = ""
    COGNITO_APP_CLIENT_IDS: str = ""
    AUTH_DISABLED: bool = False

    EMAIL_SENDER: str = "Energia <no-reply@localhost>"
    SES_REGION: str = "sa-east-1"

    CACHE_TTL_SECONDS: float = Field(default=300.0, ge=0)
    NEWSLETTER_RATE_LIMIT_PER_MINUTE: int = Field(default=5, ge=1)
    TRUST_X_FORWARDED_FOR: bool = False
    WEB_API_SECRET: str = ""

    @model_validator(mode="after")
    def _check_environment(self) -> Self:
        if self.AUTH_DISABLED and self.ENVIRONMENT != "local":
            raise ValueError("AUTH_DISABLED=true is only allowed with ENVIRONMENT=local")
        if self.ENVIRONMENT == "prod" and not self.AUTH_DISABLED:
            missing = [
                name
                for name in ("COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_IDS")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"required in prod: {', '.join(missing)}")
        return self

    @property
    def database_url(self) -> str:
        url = self.DATABASE_URL
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

    @property
    def cognito_client_ids(self) -> frozenset[str]:
        return frozenset(c.strip() for c in self.COGNITO_APP_CLIENT_IDS.split(",") if c.strip())

    @property
    def cognito_issuer(self) -> str:
        return (
            f"https://cognito-idp.{self.COGNITO_REGION}.amazonaws.com/{self.COGNITO_USER_POOL_ID}"
        )

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def web_url(self) -> str:
        return self.WEB_URL.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
