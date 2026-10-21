"""Newsletter with double opt-in.

Tokens are random (`secrets`), sent only by e-mail, and stored as sha256 hashes. The
confirmation token expires after 48h; the unsubscribe token never expires.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import SessionDep
from app.email import EmailMessage, EmailSender, send_safely
from app.models import AssinanteNewsletter
from app.ratelimit import RateLimiter
from app.schemas import (
    ConfirmadoResposta,
    DescadastradoResposta,
    MensagemResposta,
    NewsletterEntrada,
)

router = APIRouter(prefix="/v1/newsletter", tags=["newsletter"])

CONFIRMATION_TTL = timedelta(hours=48)
ACCEPTED_MESSAGE = "Se o endereço for válido, você receberá um e-mail para confirmar a inscrição."
INVALID_TOKEN = "Token inválido ou expirado."  # noqa: S105 (message, not a secret)

TokenQuery = Annotated[str, Query(max_length=200)]


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    # SQLite (tests) returns naive datetimes; Postgres returns aware ones.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def client_ip(request: Request, settings: Settings) -> str:
    if settings.CONFIAR_X_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The right-most entry is the one appended by our own load balancer.
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "desconhecido"


def confirmation_email(settings: Settings, to: str, confirm: str, unsubscribe: str) -> EmailMessage:
    confirm_url = f"{settings.web_url}/newsletter/confirmar?token={quote(confirm)}"
    unsubscribe_url = f"{settings.web_url}/newsletter/descadastrar?token={quote(unsubscribe)}"
    text = (
        "Olá!\n\n"
        "Recebemos um pedido de inscrição na newsletter do monitor de curtailment.\n"
        f"Para confirmar, acesse (válido por 48 horas):\n{confirm_url}\n\n"
        "Se não foi você, ignore este e-mail.\n"
        f"Para sair da lista a qualquer momento: {unsubscribe_url}\n"
    )
    html = (
        "<p>Olá!</p>"
        "<p>Recebemos um pedido de inscrição na newsletter do monitor de curtailment.</p>"
        f'<p><a href="{confirm_url}">Confirmar inscrição</a> (válido por 48 horas).</p>'
        "<p>Se não foi você, ignore este e-mail.</p>"
        f'<p><small><a href="{unsubscribe_url}">Descadastrar</a></small></p>'
    )
    return EmailMessage(to=to, subject="Confirme sua inscrição na newsletter", text=text, html=html)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MensagemResposta,
    responses={429: {"description": "Limite de taxa excedido"}},
)
def subscribe(
    body: NewsletterEntrada,
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
) -> MensagemResposta:
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.newsletter_limiter
    if not limiter.allow(client_ip(request, settings)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em instantes.",
            headers={"Retry-After": "60"},
        )

    email = body.email.strip().lower()
    now = datetime.now(UTC)
    subscriber = session.scalars(
        select(AssinanteNewsletter).where(AssinanteNewsletter.email == email)
    ).first()

    if subscriber is not None and subscriber.confirmado_em and not subscriber.descadastrado_em:
        # Already an active subscriber: nothing to do, same answer (no list enumeration).
        return MensagemResposta(mensagem=ACCEPTED_MESSAGE)

    confirm_token, confirm_hash = new_token()
    unsubscribe_token, unsubscribe_hash = new_token()
    if subscriber is None:
        subscriber = AssinanteNewsletter(email=email)
        session.add(subscriber)
    # New, pending or previously unsubscribed: (re)start the double opt-in.
    subscriber.consentimento_em = now
    subscriber.confirmado_em = None
    subscriber.descadastrado_em = None
    subscriber.token_confirmacao_hash = confirm_hash
    subscriber.token_confirmacao_expira_em = now + CONFIRMATION_TTL
    subscriber.token_descadastro_hash = unsubscribe_hash
    subscriber.origem = body.origem
    try:
        session.commit()
    except IntegrityError:
        # Concurrent subscription of the same address; the other request sends the e-mail.
        session.rollback()
        return MensagemResposta(mensagem=ACCEPTED_MESSAGE)

    sender: EmailSender = request.app.state.email_sender
    background.add_task(
        send_safely, sender, confirmation_email(settings, email, confirm_token, unsubscribe_token)
    )
    return MensagemResposta(mensagem=ACCEPTED_MESSAGE)


@router.get(
    "/confirmar", response_model=ConfirmadoResposta, responses={400: {"description": INVALID_TOKEN}}
)
def confirm(session: SessionDep, token: TokenQuery) -> ConfirmadoResposta:
    subscriber = session.scalars(
        select(AssinanteNewsletter).where(
            AssinanteNewsletter.token_confirmacao_hash == hash_token(token)
        )
    ).first()
    if subscriber is None:
        raise HTTPException(status_code=400, detail=INVALID_TOKEN)
    if subscriber.confirmado_em is not None and subscriber.descadastrado_em is None:
        return ConfirmadoResposta(confirmado=True)  # link clicked twice
    expires = subscriber.token_confirmacao_expira_em
    now = datetime.now(UTC)
    if expires is None or _aware(expires) <= now or subscriber.descadastrado_em is not None:
        raise HTTPException(status_code=400, detail=INVALID_TOKEN)
    subscriber.confirmado_em = now
    session.commit()
    return ConfirmadoResposta(confirmado=True)


@router.get(
    "/descadastrar",
    response_model=DescadastradoResposta,
    responses={400: {"description": INVALID_TOKEN}},
)
def unsubscribe(session: SessionDep, token: TokenQuery) -> DescadastradoResposta:
    subscriber = session.scalars(
        select(AssinanteNewsletter).where(
            AssinanteNewsletter.token_descadastro_hash == hash_token(token)
        )
    ).first()
    if subscriber is None:
        raise HTTPException(status_code=400, detail=INVALID_TOKEN)
    if subscriber.descadastrado_em is None:
        subscriber.descadastrado_em = datetime.now(UTC)
        session.commit()
    return DescadastradoResposta(descadastrado=True)
