"""FastAPI application factory. Run with `uvicorn app.main:app`."""

import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.auth import JwksCache
from app.cache import TTLCache
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.email import EmailSender, make_email_sender
from app.ratelimit import RateLimiter
from app.routers import admin, curtailment, health, newsletter, relatorios, usinas

logger = logging.getLogger("app")


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(p) for p in error.get("loc", ()) if p != "body")
        parts.append(f"{location}: {error.get('msg', 'inválido')}" if location else error["msg"])
    return "Parâmetros inválidos: " + "; ".join(parts)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Contract: errors are always {"detail": "<string>"}.
    return JSONResponse(status_code=422, content={"detail": _validation_message(exc)})


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro interno."})


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    email_sender: EmailSender | None = None,
    web_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)

    app = FastAPI(
        title="energia-api",
        version="0.1.0",
        description="API pública e administrativa da plataforma de dados de energia (Fase 1).",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.cache = TTLCache(settings.CACHE_TTL_SEGUNDOS)
    app.state.newsletter_limiter = RateLimiter(settings.NEWSLETTER_LIMITE_POR_MINUTO, 60.0)
    app.state.email_sender = email_sender or make_email_sender(settings)
    app.state.jwks = JwksCache(settings.cognito_jwks_url)
    app.state.web_transport = web_transport

    if settings.AUTH_DESATIVADA:
        logger.warning("AUTH_DESATIVADA=true: rotas /v1/admin/* sem autenticação (só local)")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_url],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    for module in (curtailment, usinas, relatorios, newsletter, admin, health):
        app.include_router(module.router)
    return app


app = create_app()
