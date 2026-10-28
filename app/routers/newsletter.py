import hmac
import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionDep
from app.email import EmailMessage, EmailSender, send_safely, template_message
from app.events import log_event
from app.models import NewsletterSubscriber
from app.ratelimit import RateLimiter
from app.schemas import (
    ConfirmedResponse,
    MessageResponse,
    NewsletterSignup,
    UnsubscribedResponse,
)
from app.security import hash_token, new_token

router = APIRouter(prefix="/v1/newsletter", tags=["newsletter"])

CONFIRMATION_TTL = timedelta(hours=48)
UNSUBSCRIBED_RETENTION = timedelta(days=30)
ACCEPTED_MESSAGE = "If the address is valid, a confirmation e-mail is on its way."
INVALID_LINK = "Invalid or expired token."

TokenQuery = Annotated[str, Query(max_length=200)]

CLIENT_IP_HEADER = "x-client-ip"
WEB_AUTH_HEADER = "x-web-api-secret"


def purge_stale_subscribers(session: Session, now: datetime) -> None:
    session.execute(
        delete(NewsletterSubscriber).where(
            or_(
                (NewsletterSubscriber.confirmed_at.is_(None))
                & (NewsletterSubscriber.confirmation_token_expires_at < now),
                NewsletterSubscriber.unsubscribed_at < now - UNSUBSCRIBED_RETENTION,
            )
        )
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _trusted_web_client_ip(request: Request, settings: Settings) -> str | None:
    if not settings.WEB_API_SECRET:
        return None
    provided = request.headers.get(WEB_AUTH_HEADER, "")
    if not hmac.compare_digest(provided.encode(), settings.WEB_API_SECRET.encode()):
        return None
    try:
        return str(ipaddress.ip_address(request.headers.get(CLIENT_IP_HEADER, "").strip()))
    except ValueError:
        return None


def client_ip(request: Request, settings: Settings) -> str:
    trusted = _trusted_web_client_ip(request, settings)
    if trusted is not None:
        return trusted
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def confirmation_email(settings: Settings, to: str, confirm: str, unsubscribe: str) -> EmailMessage:
    base = f"{settings.web_url}/newsletter"
    return template_message(
        settings,
        "newsletter-confirmation",
        to,
        {
            "confirm_url": f"{base}/confirmar?token={quote(confirm)}",
            "unsubscribe_url": f"{base}/descadastrar?token={quote(unsubscribe)}",
        },
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageResponse,
    responses={429: {"description": "Rate limit exceeded"}},
)
def subscribe(
    body: NewsletterSignup,
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
) -> MessageResponse:
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.newsletter_limiter
    if not limiter.allow(client_ip(request, settings)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again shortly.",
            headers={"Retry-After": "60"},
        )

    email = body.email.strip().lower()
    now = datetime.now(UTC)
    purge_stale_subscribers(session, now)
    subscriber = session.scalars(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == email)
    ).first()

    if subscriber is not None and subscriber.confirmed_at and not subscriber.unsubscribed_at:
        return MessageResponse(message=ACCEPTED_MESSAGE)

    confirm_token, confirm_hash = new_token()
    unsubscribe_token, unsubscribe_hash = new_token()
    if subscriber is None:
        subscriber = NewsletterSubscriber(email=email)
        session.add(subscriber)
    subscriber.consented_at = now
    subscriber.confirmed_at = None
    subscriber.unsubscribed_at = None
    subscriber.confirmation_token_hash = confirm_hash
    subscriber.confirmation_token_expires_at = now + CONFIRMATION_TTL
    subscriber.unsubscribe_token_hash = unsubscribe_hash
    subscriber.signup_source = body.source
    log_event(
        session,
        request,
        category="newsletter",
        action="newsletter.subscribe",
        properties={"source": body.source},
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return MessageResponse(message=ACCEPTED_MESSAGE)

    sender: EmailSender = request.app.state.email_sender
    background.add_task(
        send_safely, sender, confirmation_email(settings, email, confirm_token, unsubscribe_token)
    )
    return MessageResponse(message=ACCEPTED_MESSAGE)


@router.get(
    "/confirm", response_model=ConfirmedResponse, responses={400: {"description": INVALID_LINK}}
)
def confirm(request: Request, session: SessionDep, token: TokenQuery) -> ConfirmedResponse:
    subscriber = session.scalars(
        select(NewsletterSubscriber).where(
            NewsletterSubscriber.confirmation_token_hash == hash_token(token)
        )
    ).first()
    if subscriber is None:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    if subscriber.confirmed_at is not None and subscriber.unsubscribed_at is None:
        return ConfirmedResponse(confirmed=True)
    expires = subscriber.confirmation_token_expires_at
    now = datetime.now(UTC)
    if expires is None or _aware(expires) <= now or subscriber.unsubscribed_at is not None:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    subscriber.confirmed_at = now
    log_event(session, request, category="newsletter", action="newsletter.confirm")
    session.commit()
    return ConfirmedResponse(confirmed=True)


@router.get(
    "/unsubscribe",
    response_model=UnsubscribedResponse,
    responses={400: {"description": INVALID_LINK}},
)
def unsubscribe(request: Request, session: SessionDep, token: TokenQuery) -> UnsubscribedResponse:
    subscriber = session.scalars(
        select(NewsletterSubscriber).where(
            NewsletterSubscriber.unsubscribe_token_hash == hash_token(token)
        )
    ).first()
    if subscriber is None:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    if subscriber.unsubscribed_at is None:
        subscriber.unsubscribed_at = datetime.now(UTC)
        log_event(session, request, category="newsletter", action="newsletter.unsubscribe")
        session.commit()
    return UnsubscribedResponse(unsubscribed=True)
