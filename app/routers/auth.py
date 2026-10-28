import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionDep
from app.email import EmailMessage, EmailSender, send_safely, template_message
from app.events import log_event
from app.leads import score_lead
from app.models import Member, MemberSession, MemberToken, UsageEvent
from app.ratelimit import RateLimiter
from app.routers.newsletter import client_ip
from app.schemas import (
    EmailOnly,
    EmailVerification,
    LoginResponse,
    MemberLogin,
    MemberProfile,
    MemberSignup,
    MessageResponse,
    PasswordConfirmation,
    PasswordReset,
    SignupResponse,
    VerifiedResponse,
)
from app.security import DUMMY_HASH, hash_password, hash_token, new_token, verify_password

router = APIRouter(prefix="/v1/auth", tags=["auth"])

VERIFY_CODE_TTL = timedelta(minutes=30)
MAX_CODE_ATTEMPTS = 5
CODE_DIGITS = 6
RESET_CODE_TTL = timedelta(minutes=30)
SESSION_TTL = timedelta(days=14)
ACCEPTED_MESSAGE = "If the address is valid, an e-mail is on its way."
INVALID_CODE = "Invalid or expired code."
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


def _code_hash(member: Member, kind: str, code: str) -> str:
    return hash_token(f"{member.id}:{kind}:{code}")


def _issue_code(session: Session, member: Member, kind: str, ttl: timedelta) -> str:
    session.execute(
        delete(MemberToken).where(MemberToken.member_id == member.id, MemberToken.kind == kind)
    )
    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    session.add(
        MemberToken(
            member_id=member.id,
            kind=kind,
            token_hash=_code_hash(member, kind, code),
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    return code


def _dev_code(settings: Settings, code: str) -> str | None:
    local_console = settings.ENVIRONMENT == "local" and settings.EMAIL_BACKEND in ("", "console")
    return code if local_console else None


def _consume_code(
    session: Session, request: Request, member: Member, kind: str, code: str, failed_action: str
) -> MemberToken:
    """Checks a 6-digit code; counts a wrong guess and locks the code after MAX_CODE_ATTEMPTS."""
    token = session.scalars(
        select(MemberToken)
        .where(
            MemberToken.member_id == member.id,
            MemberToken.kind == kind,
            MemberToken.used_at.is_(None),
        )
        .order_by(MemberToken.id.desc())
    ).first()
    now = datetime.now(UTC)
    if token is None or _aware(token.expires_at) <= now or token.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(status_code=400, detail=INVALID_CODE)
    if not hmac.compare_digest(token.token_hash, _code_hash(member, kind, code)):
        token.attempts += 1
        log_event(
            session,
            request,
            category="auth",
            action=failed_action,
            member=member,
            properties={"attempts": token.attempts},
        )
        session.commit()
        raise HTTPException(status_code=400, detail=INVALID_CODE)
    token.used_at = now
    return token


def verification_email(settings: Settings, to: str, name: str, code: str) -> EmailMessage:
    minutes = int(VERIFY_CODE_TTL.total_seconds() // 60)
    return template_message(
        settings,
        "verification-code",
        to,
        {"name": first_name(name), "code": code},
        fixed={"minutes": minutes},
    )


def existing_account_email(settings: Settings, to: str, name: str) -> EmailMessage:
    return template_message(settings, "existing-account", to, {"name": first_name(name)})


def reset_email(settings: Settings, to: str, name: str, code: str) -> EmailMessage:
    minutes = int(RESET_CODE_TTL.total_seconds() // 60)
    return template_message(
        settings,
        "reset-code",
        to,
        {"name": first_name(name), "code": code},
        fixed={"minutes": minutes},
    )


def first_name(name: str) -> str:
    parts = name.split()
    return parts[0] if parts else "assinante"


@router.post(
    "/signup",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SignupResponse,
    responses={429: {"description": "Rate limit exceeded"}},
)
def signup(
    body: MemberSignup, request: Request, session: SessionDep, background: BackgroundTasks
) -> SignupResponse:
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
        background.add_task(
            send_safely, sender, existing_account_email(settings, email, existing.name)
        )
        return SignupResponse(message=ACCEPTED_MESSAGE)
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
    code = _issue_code(session, member, "verify", VERIFY_CODE_TTL)
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
        return SignupResponse(message=ACCEPTED_MESSAGE)
    background.add_task(send_safely, sender, verification_email(settings, email, body.name, code))
    return SignupResponse(message=ACCEPTED_MESSAGE, dev_code=_dev_code(settings, code))


@router.post("/verify-email", response_model=VerifiedResponse)
def verify_email(
    body: EmailVerification, request: Request, session: SessionDep
) -> VerifiedResponse:
    email = str(body.email).strip().lower()
    _throttle(request, "verify", email)
    member = session.scalars(select(Member).where(Member.email == email)).first()
    if member is None or member.email_verified_at is not None:
        raise HTTPException(status_code=400, detail=INVALID_CODE)
    _consume_code(session, request, member, "verify", body.code, "auth.verify_failed")
    member.email_verified_at = datetime.now(UTC)
    log_event(session, request, category="auth", action="auth.verify", member=member)
    session.commit()
    return VerifiedResponse(verified=True)


@router.post("/resend-verification", status_code=202, response_model=SignupResponse)
def resend_verification(
    body: EmailOnly, request: Request, session: SessionDep, background: BackgroundTasks
) -> SignupResponse:
    email = str(body.email).strip().lower()
    _throttle(request, "resend", email)
    settings: Settings = request.app.state.settings
    sender: EmailSender = request.app.state.email_sender
    member = session.scalars(select(Member).where(Member.email == email)).first()
    if member is not None and member.email_verified_at is None:
        code = _issue_code(session, member, "verify", VERIFY_CODE_TTL)
        log_event(
            session, request, category="auth", action="auth.resend_verification", member=member
        )
        session.commit()
        background.add_task(
            send_safely, sender, verification_email(settings, email, member.name, code)
        )
        return SignupResponse(message=ACCEPTED_MESSAGE, dev_code=_dev_code(settings, code))
    return SignupResponse(message=ACCEPTED_MESSAGE)


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


@router.post("/forgot-password", status_code=202, response_model=SignupResponse)
def forgot_password(
    body: EmailOnly, request: Request, session: SessionDep, background: BackgroundTasks
) -> SignupResponse:
    email = str(body.email).strip().lower()
    _throttle(request, "forgot", email)
    settings: Settings = request.app.state.settings
    sender: EmailSender = request.app.state.email_sender
    member = session.scalars(select(Member).where(Member.email == email)).first()
    if member is not None and member.email_verified_at is not None:
        code = _issue_code(session, member, "reset", RESET_CODE_TTL)
        log_event(
            session, request, category="auth", action="auth.password_reset_requested", member=member
        )
        session.commit()
        background.add_task(send_safely, sender, reset_email(settings, email, member.name, code))
        return SignupResponse(message=ACCEPTED_MESSAGE, dev_code=_dev_code(settings, code))
    return SignupResponse(message=ACCEPTED_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(body: PasswordReset, request: Request, session: SessionDep) -> MessageResponse:
    email = str(body.email).strip().lower()
    _throttle(request, "reset", email)
    member = session.scalars(select(Member).where(Member.email == email)).first()
    if member is None or member.email_verified_at is None:
        raise HTTPException(status_code=400, detail=INVALID_CODE)
    _consume_code(session, request, member, "reset", body.code, "auth.password_reset_failed")
    member.password_hash = hash_password(body.password)
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
