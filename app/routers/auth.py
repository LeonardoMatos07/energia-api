from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionDep
from app.email import EmailMessage, EmailSender, send_safely
from app.events import log_event
from app.leads import score_lead
from app.models import Member, MemberSession, MemberToken, UsageEvent
from app.ratelimit import RateLimiter
from app.routers.newsletter import client_ip
from app.schemas import (
    EmailOnly,
    LoginResponse,
    MemberLogin,
    MemberProfile,
    MemberSignup,
    MessageResponse,
    PasswordConfirmation,
    PasswordReset,
    TokenOnly,
    VerifiedResponse,
)
from app.security import DUMMY_HASH, hash_password, hash_token, new_token, verify_password

router = APIRouter(prefix="/v1/auth", tags=["auth"])

VERIFY_TTL = timedelta(hours=48)
RESET_TTL = timedelta(hours=1)
SESSION_TTL = timedelta(days=14)
ACCEPTED_MESSAGE = "If the address is valid, an e-mail is on its way."
INVALID_LINK = "Invalid or expired token."
INVALID_CREDENTIALS = "Invalid e-mail or password."
UNVERIFIED = "Email not verified."
UNAUTHORIZED = "Authentication required."


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _throttle(request: Request, action: str, identity: str | None = None) -> None:
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.auth_limiter
    keys = [f"{action}:ip:{client_ip(request, settings)}"]
    if identity:
        keys.append(f"{action}:id:{identity}")
    if not all(limiter.allow(key) for key in keys):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again shortly.",
            headers={"Retry-After": "60"},
        )


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def member_from_token(session: Session, authorization: str | None) -> Member | None:
    token = _bearer(authorization)
    if token is None:
        return None
    row = session.execute(
        select(Member, MemberSession)
        .join(MemberSession, MemberSession.member_id == Member.id)
        .where(MemberSession.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    member, member_session = row
    if _aware(member_session.expires_at) <= datetime.now(UTC) or member.email_verified_at is None:
        return None
    return member


def get_current_member(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> Member:
    member = member_from_token(session, authorization)
    if member is None:
        raise HTTPException(status_code=401, detail=UNAUTHORIZED)
    return member


CurrentMember = Annotated[Member, Depends(get_current_member)]


def _delete_member(session: Session, member: Member) -> None:
    session.execute(
        update(UsageEvent)
        .where(UsageEvent.member_id == member.id)
        .values(member_id=None, visitor_id=None, ip_hash=None, user_agent=None)
    )
    session.execute(delete(MemberToken).where(MemberToken.member_id == member.id))
    session.execute(delete(MemberSession).where(MemberSession.member_id == member.id))
    session.delete(member)


def _issue_token(session: Session, member: Member, kind: str, ttl: timedelta) -> str:
    session.execute(
        delete(MemberToken).where(MemberToken.member_id == member.id, MemberToken.kind == kind)
    )
    token, token_hash = new_token()
    session.add(
        MemberToken(
            member_id=member.id,
            kind=kind,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    return token


def _find_token(session: Session, raw: str, kind: str) -> tuple[MemberToken, Member] | None:
    row = session.execute(
        select(MemberToken, Member)
        .join(Member, Member.id == MemberToken.member_id)
        .where(MemberToken.token_hash == hash_token(raw), MemberToken.kind == kind)
    ).first()
    return None if row is None else (row[0], row[1])


def verification_email(settings: Settings, to: str, token: str) -> EmailMessage:
    url = f"{settings.web_url}/verificar-email?token={quote(token)}"
    text = (
        "Olá!\n\n"
        "Recebemos um cadastro no Spillway com este e-mail.\n"
        f"Para confirmar e liberar seu acesso gratuito, acesse (válido por 48 horas):\n{url}\n\n"
        "Se não foi você, ignore este e-mail: nada será ativado.\n"
    )
    html = (
        "<p>Olá!</p>"
        "<p>Recebemos um cadastro no Spillway com este e-mail.</p>"
        f'<p><a href="{url}">Confirmar e-mail e liberar o acesso gratuito</a> '
        "(válido por 48 horas).</p>"
        "<p>Se não foi você, ignore este e-mail: nada será ativado.</p>"
    )
    return EmailMessage(
        to=to, subject="Confirme seu e-mail para acessar o Spillway", text=text, html=html
    )


def existing_account_email(settings: Settings, to: str) -> EmailMessage:
    login_url = f"{settings.web_url}/entrar"
    reset_url = f"{settings.web_url}/esqueci-a-senha"
    text = (
        "Olá!\n\n"
        "Alguém tentou criar um cadastro no Spillway com este e-mail, que já tem conta.\n"
        f"Para entrar: {login_url}\n"
        f"Se esqueceu a senha: {reset_url}\n\n"
        "Se não foi você, ignore este e-mail.\n"
    )
    html = (
        "<p>Olá!</p>"
        "<p>Alguém tentou criar um cadastro no Spillway com este e-mail, que já tem conta.</p>"
        f'<p><a href="{login_url}">Entrar</a> · <a href="{reset_url}">Esqueci a senha</a></p>'
        "<p>Se não foi você, ignore este e-mail.</p>"
    )
    return EmailMessage(to=to, subject="Você já tem conta no Spillway", text=text, html=html)


def reset_email(settings: Settings, to: str, token: str) -> EmailMessage:
    url = f"{settings.web_url}/redefinir-senha?token={quote(token)}"
    text = (
        "Olá!\n\n"
        "Recebemos um pedido para redefinir a senha do Spillway.\n"
        f"Para escolher uma nova senha, acesse (válido por 1 hora):\n{url}\n\n"
        "Se não foi você, ignore este e-mail: sua senha continua a mesma.\n"
    )
    html = (
        "<p>Olá!</p>"
        "<p>Recebemos um pedido para redefinir a senha do Spillway.</p>"
        f'<p><a href="{url}">Escolher nova senha</a> (válido por 1 hora).</p>'
        "<p>Se não foi você, ignore este e-mail: sua senha continua a mesma.</p>"
    )
    return EmailMessage(to=to, subject="Redefinir a senha do Spillway", text=text, html=html)


@router.post(
    "/signup",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageResponse,
    responses={429: {"description": "Rate limit exceeded"}},
)
def signup(
    body: MemberSignup, request: Request, session: SessionDep, background: BackgroundTasks
) -> MessageResponse:
    _throttle(request, "signup")
    settings: Settings = request.app.state.settings
    sender: EmailSender = request.app.state.email_sender
    email = str(body.email).strip().lower()
    password_hash = hash_password(body.password)
    now = datetime.now(UTC)

    existing = session.scalars(select(Member).where(Member.email == email)).first()
    if existing is not None and existing.email_verified_at is not None:
        log_event(session, request, category="auth", action="auth.signup_existing")
        session.commit()
        background.add_task(send_safely, sender, existing_account_email(settings, email))
        return MessageResponse(message=ACCEPTED_MESSAGE)
    if existing is not None:
        _delete_member(session, existing)
        session.flush()

    score, tier = score_lead(
        role=body.role,
        email=email,
        organization=body.organization,
        segment=body.segment,
        company_size=body.company_size,
        plants=body.plants,
        interests=body.interests,
        phone=body.phone,
        marketing_opt_in=body.marketing_opt_in,
    )
    member = Member(
        email=email,
        password_hash=password_hash,
        name=body.name,
        role=body.role,
        organization=body.organization,
        job_title=body.job_title,
        segment=body.segment,
        company_size=body.company_size,
        institution=body.institution,
        study_level=body.study_level,
        regions=body.regions,
        plants=body.plants,
        interests=body.interests,
        phone=body.phone,
        referral=body.referral,
        signup_source=body.source,
        marketing_opt_in=body.marketing_opt_in,
        privacy_consent_at=now,
        lead_score=score,
        lead_tier=tier,
    )
    session.add(member)
    session.flush()
    token = _issue_token(session, member, "verify", VERIFY_TTL)
    log_event(
        session,
        request,
        category="auth",
        action="auth.signup",
        member=member,
        properties={
            "lead_tier": tier,
            "lead_score": score,
            "regions": body.regions,
            "plants": len(body.plants),
            "interests": body.interests,
            "referral": body.referral,
            "marketing_opt_in": body.marketing_opt_in,
            "source": body.source,
        },
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return MessageResponse(message=ACCEPTED_MESSAGE)
    background.add_task(send_safely, sender, verification_email(settings, email, token))
    return MessageResponse(message=ACCEPTED_MESSAGE)


@router.post("/verify-email", response_model=VerifiedResponse)
def verify_email(body: TokenOnly, request: Request, session: SessionDep) -> VerifiedResponse:
    _throttle(request, "verify")
    found = _find_token(session, body.token, "verify")
    if found is None:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    token, member = found
    now = datetime.now(UTC)
    if member.email_verified_at is not None:
        return VerifiedResponse(verified=True)
    if token.used_at is not None or _aware(token.expires_at) <= now:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    token.used_at = now
    member.email_verified_at = now
    log_event(session, request, category="auth", action="auth.verify", member=member)
    session.commit()
    return VerifiedResponse(verified=True)


@router.post("/resend-verification", status_code=202, response_model=MessageResponse)
def resend_verification(
    body: EmailOnly, request: Request, session: SessionDep, background: BackgroundTasks
) -> MessageResponse:
    email = str(body.email).strip().lower()
    _throttle(request, "resend", email)
    settings: Settings = request.app.state.settings
    sender: EmailSender = request.app.state.email_sender
    member = session.scalars(select(Member).where(Member.email == email)).first()
    if member is not None and member.email_verified_at is None:
        token = _issue_token(session, member, "verify", VERIFY_TTL)
        log_event(
            session, request, category="auth", action="auth.resend_verification", member=member
        )
        session.commit()
        background.add_task(send_safely, sender, verification_email(settings, email, token))
    return MessageResponse(message=ACCEPTED_MESSAGE)


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"description": INVALID_CREDENTIALS}, 403: {"description": UNVERIFIED}},
)
def login(body: MemberLogin, request: Request, session: SessionDep) -> LoginResponse:
    email = str(body.email).strip().lower()
    _throttle(request, "login", email)
    member = session.scalars(select(Member).where(Member.email == email)).first()
    password_ok = verify_password(
        body.password, member.password_hash if member is not None else DUMMY_HASH
    )
    if member is None or not password_ok:
        log_event(
            session,
            request,
            category="auth",
            action="auth.login_failed",
            member=member,
            properties={"reason": "invalid_credentials"},
        )
        session.commit()
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)
    if member.email_verified_at is None:
        log_event(
            session,
            request,
            category="auth",
            action="auth.login_failed",
            member=member,
            properties={"reason": "unverified"},
        )
        session.commit()
        raise HTTPException(status_code=403, detail=UNVERIFIED)

    now = datetime.now(UTC)
    token, token_hash = new_token()
    expires_at = now + SESSION_TTL
    session.execute(
        delete(MemberSession).where(
            MemberSession.member_id == member.id, MemberSession.expires_at < now
        )
    )
    session.add(MemberSession(member_id=member.id, token_hash=token_hash, expires_at=expires_at))
    member.last_login_at = now
    log_event(session, request, category="auth", action="auth.login", member=member)
    session.commit()
    return LoginResponse(
        session_token=token, expires_at=expires_at, member=MemberProfile.model_validate(member)
    )


@router.get("/me", response_model=MemberProfile, responses={401: {"description": UNAUTHORIZED}})
def me(member: CurrentMember) -> MemberProfile:
    return MemberProfile.model_validate(member)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> MessageResponse:
    token = _bearer(authorization)
    if token is not None:
        member = member_from_token(session, authorization)
        session.execute(delete(MemberSession).where(MemberSession.token_hash == hash_token(token)))
        log_event(session, request, category="auth", action="auth.logout", member=member)
        session.commit()
    return MessageResponse(message="Signed out.")


@router.post("/forgot-password", status_code=202, response_model=MessageResponse)
def forgot_password(
    body: EmailOnly, request: Request, session: SessionDep, background: BackgroundTasks
) -> MessageResponse:
    email = str(body.email).strip().lower()
    _throttle(request, "forgot", email)
    settings: Settings = request.app.state.settings
    sender: EmailSender = request.app.state.email_sender
    member = session.scalars(select(Member).where(Member.email == email)).first()
    if member is not None and member.email_verified_at is not None:
        token = _issue_token(session, member, "reset", RESET_TTL)
        log_event(
            session, request, category="auth", action="auth.password_reset_requested", member=member
        )
        session.commit()
        background.add_task(send_safely, sender, reset_email(settings, email, token))
    return MessageResponse(message=ACCEPTED_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(body: PasswordReset, request: Request, session: SessionDep) -> MessageResponse:
    _throttle(request, "reset")
    found = _find_token(session, body.token, "reset")
    if found is None:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    token, member = found
    now = datetime.now(UTC)
    if token.used_at is not None or _aware(token.expires_at) <= now:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    token.used_at = now
    member.password_hash = hash_password(body.password)
    if member.email_verified_at is None:
        member.email_verified_at = now
    session.execute(delete(MemberSession).where(MemberSession.member_id == member.id))
    log_event(session, request, category="auth", action="auth.password_reset", member=member)
    session.commit()
    return MessageResponse(message="Password updated.")


@router.post("/me/delete", response_model=MessageResponse)
def delete_account(
    body: PasswordConfirmation, request: Request, member: CurrentMember, session: SessionDep
) -> MessageResponse:
    _throttle(request, "delete", str(member.id))
    if not verify_password(body.password, member.password_hash):
        raise HTTPException(status_code=403, detail=INVALID_CREDENTIALS)
    log_event(
        session,
        request,
        category="auth",
        action="auth.account_deleted",
        properties={"role": member.role, "lead_tier": member.lead_tier},
        anonymous=True,
    )
    _delete_member(session, member)
    session.commit()
    return MessageResponse(message="Account deleted.")
