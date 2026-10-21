"""Application settings, read from environment variables (names follow the Phase 1 contract)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg://warehouse:warehouse@localhost:5433/warehouse"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    AMBIENTE: Literal["local", "prod"] = "local"
    DATABASE_URL: str = DEFAULT_DATABASE_URL

    WEB_URL: str = "http://localhost:3000"
    REVALIDATE_SECRET: str = ""
    REVALIDATE_TIMEOUT_SEGUNDOS: float = 5.0

    COGNITO_REGION: str = "sa-east-1"
    COGNITO_USER_POOL_ID: str = ""
    # Comma-separated list of allowed app client ids.
    COGNITO_APP_CLIENT_IDS: str = ""
    AUTH_DESATIVADA: bool = False

    EMAIL_REMETENTE: str = "Energia <nao-responda@localhost>"
    SES_REGION: str = "sa-east-1"

    CACHE_TTL_SEGUNDOS: float = Field(default=300.0, ge=0)
    NEWSLETTER_LIMITE_POR_MINUTO: int = Field(default=5, ge=1)
    # Only enable behind a proxy/load balancer that appends the client IP to X-Forwarded-For.
    CONFIAR_X_FORWARDED_FOR: bool = False

    @model_validator(mode="after")
    def _check_environment(self) -> Self:
        if self.AUTH_DESATIVADA and self.AMBIENTE != "local":
            raise ValueError("AUTH_DESATIVADA=true só é permitido com AMBIENTE=local")
        if self.AMBIENTE == "prod" and not self.AUTH_DESATIVADA:
            missing = [
                name
                for name in ("COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_IDS")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"variáveis obrigatórias em prod: {', '.join(missing)}")
        return self

    @property
    def database_url(self) -> str:
        """DATABASE_URL normalized to the psycopg (v3) SQLAlchemy driver."""
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
