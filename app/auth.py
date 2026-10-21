"""Admin authentication: Cognito access tokens (RS256) validated against the pool JWKS."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

logger = logging.getLogger(__name__)

ADMIN_GROUP = "admin"
ADMIN_SCOPE = "energia-api/admin"

JwksFetcher = Callable[[str], dict[str, Any]]


def fetch_jwks(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data


class JwksCache:
    """Caches the pool's signing keys; refetches on unknown `kid` (at most once per minute)."""

    def __init__(
        self,
        url: str,
        fetcher: JwksFetcher = fetch_jwks,
        ttl_seconds: float = 3600.0,
        min_refresh_seconds: float = 60.0,
    ) -> None:
        self.url = url
        self.fetcher = fetcher
        self.ttl = ttl_seconds
        self.min_refresh = min_refresh_seconds
        self._keys: dict[str, Any] = {}
        self._fetched_at = float("-inf")
        self._lock = threading.Lock()

    def get_key(self, kid: str) -> Any:
        with self._lock:
            now = time.monotonic()
            expired = now - self._fetched_at > self.ttl
            unknown = kid not in self._keys and now - self._fetched_at > self.min_refresh
            if expired or unknown:
                self._refresh(now)
            key = self._keys.get(kid)
        if key is None:
            raise jwt.InvalidTokenError("chave de assinatura desconhecida")
        return key

    def _refresh(self, now: float) -> None:
        jwks = self.fetcher(self.url)
        keys: dict[str, Any] = {}
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") and jwk.get("kty") == "RSA":
                keys[jwk["kid"]] = jwt.PyJWK(jwk, algorithm="RS256").key
        self._keys = keys
        self._fetched_at = now


@dataclass(frozen=True)
class AdminPrincipal:
    subject: str
    kind: Literal["pessoa", "maquina", "local"]


_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_admin_token(token: str, settings: Settings, jwks: JwksCache) -> AdminPrincipal:
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise jwt.InvalidTokenError("algoritmo não permitido")
        key = jwks.get_key(str(header.get("kid", "")))
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            options={"require": ["exp", "iat", "iss", "token_use"], "verify_aud": False},
            leeway=30,
        )
    except httpx.HTTPError as exc:
        logger.error("falha ao obter o JWKS do Cognito: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível validar o token agora.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        logger.info("token de admin rejeitado: %s", exc)
        raise _unauthorized("Token inválido.") from exc

    if claims.get("token_use") != "access":
        raise _unauthorized("Token inválido.")
    if claims.get("client_id") not in settings.cognito_client_ids:
        raise _unauthorized("Token inválido.")

    groups = claims.get("cognito:groups") or []
    scopes = str(claims.get("scope", "")).split()
    subject = str(claims.get("sub") or claims.get("client_id"))
    if isinstance(groups, list) and ADMIN_GROUP in groups:
        return AdminPrincipal(subject=subject, kind="pessoa")
    if ADMIN_SCOPE in scopes:
        return AdminPrincipal(subject=subject, kind="maquina")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a admin.")


def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AdminPrincipal:
    settings: Settings = request.app.state.settings
    if settings.AUTH_DESATIVADA:
        # Settings refuses this flag outside AMBIENTE=local.
        return AdminPrincipal(subject="local", kind="local")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Autenticação necessária.")
    jwks: JwksCache = request.app.state.jwks
    return verify_admin_token(credentials.credentials, settings, jwks)


AdminDep = Annotated[AdminPrincipal, Depends(require_admin)]
