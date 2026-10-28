import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Member, MemberSession, MemberToken
from app.security import hash_password, verify_password
from tests.conftest import FakeEmailSender

PASSWORD = "correct horse battery"
EMAIL = "ana@empresa.com.br"
NEW_PASSWORD = "a different password"


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": "Ana@Empresa.com.br",
        "password": PASSWORD,
        "name": "Ana Souza",
        "role": "professional",
        "organization": "Parque Eólico SA",
        "job_title": "Analista",
        "segment": "generator",
        "company_size": "201-1000",
        "regions": ["Nordeste"],
        "plants": ["sao-fernando-4-rnesf4"],
        "interests": ["monitor_losses", "storage"],
        "marketing_opt_in": True,
        "consent": True,
    }
    body.update(overrides)
    return body


def token_from(emails: FakeEmailSender, index: int = -1) -> str:
    match = re.search(r"token=([^\s&\"]+)", emails.sent[index].text)
    assert match, emails.sent[index].text
    return match.group(1)


def code_from(emails: FakeEmailSender, index: int = -1) -> str:
    match = re.search(r"código de verificação é: (\d{6})", emails.sent[index].text)
    assert match, emails.sent[index].text
    return match.group(1)


def reset_code_from(emails: FakeEmailSender, index: int = -1) -> str:
    match = re.search(r"código para criar uma nova senha é: (\d{6})", emails.sent[index].text)
    assert match, emails.sent[index].text
    return match.group(1)


def reset(client: TestClient, code: str, password: str = NEW_PASSWORD, email: str = EMAIL) -> Any:
    return client.post(
        "/v1/auth/reset-password", json={"email": email, "code": code, "password": password}
    )


def verify(client: TestClient, emails: FakeEmailSender, email: str = "ana@empresa.com.br") -> Any:
    return client.post("/v1/auth/verify-email", json={"email": email, "code": code_from(emails)})


def signup_and_verify(client: TestClient, emails: FakeEmailSender, **overrides: Any) -> None:
    body = payload(**overrides)
    assert client.post("/v1/auth/signup", json=body).status_code == 202
    assert verify(client, emails, str(body["email"]).lower()).status_code == 200


def login(client: TestClient, email: str = "ana@empresa.com.br", password: str = PASSWORD) -> Any:
    return client.post("/v1/auth/login", json={"email": email, "password": password})


def test_password_hashing_round_trip() -> None:
    stored = hash_password("a long enough secret")
    assert stored.startswith("scrypt$")
    assert verify_password("a long enough secret", stored)
    assert not verify_password("another secret", stored)
    assert not verify_password("a long enough secret", "garbage")


def test_signup_sends_a_six_digit_code_and_blocks_login_until_verified(
    client: TestClient, emails: FakeEmailSender
) -> None:
    response = client.post("/v1/auth/signup", json=payload())
    assert response.status_code == 202
    assert len(emails.sent) == 1
    assert emails.sent[0].to == "ana@empresa.com.br"
    code = code_from(emails)
    assert re.fullmatch(r"\d{6}", code)
    assert code in emails.sent[0].subject
    assert "token=" not in emails.sent[0].text

    assert login(client).status_code == 403

    verified = verify(client, emails)
    assert verified.json() == {"verified": True}
    assert login(client).status_code == 200


def test_the_code_is_only_returned_for_local_console_mail(
    app_factory: Any, emails: FakeEmailSender
) -> None:
    local = app_factory()
    assert local.post("/v1/auth/signup", json=payload()).json()["dev_code"] == code_from(emails)

    smtp = app_factory(EMAIL_BACKEND="smtp", SMTP_HOST="smtp.example.org")
    response = smtp.post("/v1/auth/signup", json=payload(email="other@empresa.com.br"))
    assert response.json()["dev_code"] is None


def test_a_wrong_code_is_refused_and_locks_after_five_attempts(
    client: TestClient, emails: FakeEmailSender
) -> None:
    client.post("/v1/auth/signup", json=payload())
    real = code_from(emails)
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        response = client.post(
            "/v1/auth/verify-email", json={"email": "ana@empresa.com.br", "code": wrong}
        )
        assert response.status_code == 400
    locked = client.post(
        "/v1/auth/verify-email", json={"email": "ana@empresa.com.br", "code": real}
    )
    assert locked.status_code == 400

    client.post("/v1/auth/resend-verification", json={"email": "ana@empresa.com.br"})
    assert verify(client, emails).status_code == 200


def test_an_expired_code_is_refused(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    client.post("/v1/auth/signup", json=payload())
    token = session.scalars(select(MemberToken)).one()
    token.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    assert verify(client, emails).status_code == 400


def test_verification_does_not_reveal_which_accounts_exist(
    client: TestClient, emails: FakeEmailSender
) -> None:
    unknown = client.post(
        "/v1/auth/verify-email", json={"email": "ghost@example.com", "code": "123456"}
    )
    assert unknown.status_code == 400
    signup_and_verify(client, emails)
    again = client.post(
        "/v1/auth/verify-email", json={"email": "ana@empresa.com.br", "code": "123456"}
    )
    assert again.status_code == 400
    assert again.json() == unknown.json()


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "12 456"])
def test_the_code_must_have_six_digits(client: TestClient, code: str) -> None:
    response = client.post(
        "/v1/auth/verify-email", json={"email": "ana@empresa.com.br", "code": code}
    )
    assert response.status_code == 422


def test_login_me_and_logout(client: TestClient, emails: FakeEmailSender) -> None:
    signup_and_verify(client, emails)
    session = login(client).json()
    headers = {"Authorization": f"Bearer {session['session_token']}"}

    profile = client.get("/v1/auth/me", headers=headers)
    assert profile.status_code == 200
    body = profile.json()
    assert body["email"] == "ana@empresa.com.br"
    assert body["role"] == "professional"
    assert body["regions"] == ["Nordeste"]
    assert "password_hash" not in body

    assert client.post("/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/v1/auth/me", headers=headers).status_code == 401


def test_me_requires_a_valid_session(client: TestClient) -> None:
    assert client.get("/v1/auth/me").status_code == 401
    assert client.get("/v1/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_wrong_credentials_do_not_reveal_whether_the_account_exists(
    client: TestClient, emails: FakeEmailSender
) -> None:
    signup_and_verify(client, emails)
    wrong = login(client, password="not the password")
    unknown = login(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_signup_for_an_existing_account_says_so_and_sends_nothing(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    signup_and_verify(client, emails)
    before = len(emails.sent)
    again = client.post("/v1/auth/signup", json=payload(name="Someone Else"))
    assert again.status_code == 409
    assert again.json() == {"detail": "An account with this e-mail already exists."}
    assert len(emails.sent) == before
    assert session.scalar(select(func.count()).select_from(Member)) == 1
    assert session.scalars(select(Member.name)).one() == "Ana Souza"


def test_resubmitting_an_unverified_signup_invalidates_the_old_code(
    client: TestClient, emails: FakeEmailSender
) -> None:
    client.post("/v1/auth/signup", json=payload())
    old = code_from(emails)
    client.post("/v1/auth/signup", json=payload(password="a brand new password"))
    new = code_from(emails)
    if old != new:
        assert (
            client.post(
                "/v1/auth/verify-email", json={"email": "ana@empresa.com.br", "code": old}
            ).status_code
            == 400
        )
    assert verify(client, emails).status_code == 200
    assert login(client, password="a brand new password").status_code == 200
    assert login(client).status_code == 401


def test_resend_verification_issues_a_new_code(client: TestClient, emails: FakeEmailSender) -> None:
    client.post("/v1/auth/signup", json=payload())
    first = code_from(emails)
    response = client.post("/v1/auth/resend-verification", json={"email": "ana@empresa.com.br"})
    assert response.status_code == 202
    assert len(emails.sent) == 2
    second = code_from(emails)
    if first != second:
        assert (
            client.post(
                "/v1/auth/verify-email", json={"email": "ana@empresa.com.br", "code": first}
            ).status_code
            == 400
        )
    assert verify(client, emails).status_code == 200


def test_resend_does_not_reveal_unknown_addresses(
    client: TestClient, emails: FakeEmailSender
) -> None:
    response = client.post("/v1/auth/resend-verification", json={"email": "ghost@example.com"})
    assert response.status_code == 202
    assert emails.sent == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": "professional", "organization": None},
        {"role": "investor", "organization": ""},
        {"role": "student", "institution": None},
        {"role": "researcher", "institution": None},
        {"consent": False},
        {"password": "short"},
        {"email": "not-an-email"},
        {"regions": ["Atlantis"]},
        {"role": "wizard"},
        {"phone": "abc"},
        {"plants": ["a", "b", "c", "d", "e", "f"]},
    ],
)
def test_signup_validation(client: TestClient, overrides: dict[str, Any]) -> None:
    assert client.post("/v1/auth/signup", json=payload(**overrides)).status_code == 422


def test_students_and_researchers_need_an_institution_not_a_company(
    client: TestClient, emails: FakeEmailSender
) -> None:
    body = payload(
        role="student",
        organization=None,
        segment=None,
        company_size=None,
        institution="UFRN",
        study_level="master",
        interests=["research"],
    )
    assert client.post("/v1/auth/signup", json=body).status_code == 202


def test_signup_scores_the_lead(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    client.post("/v1/auth/signup", json=payload())
    member = session.scalars(select(Member)).one()
    assert member.lead_tier == "hot"
    assert member.lead_score >= 60
    assert member.privacy_consent_at is not None
    assert member.email_verified_at is None


def test_password_reset_flow(client: TestClient, emails: FakeEmailSender) -> None:
    signup_and_verify(client, emails)
    old_session = login(client).json()["session_token"]

    forgot = client.post("/v1/auth/forgot-password", json={"email": EMAIL})
    assert forgot.status_code == 202
    code = reset_code_from(emails)
    assert code in emails.sent[-1].subject
    assert "token=" not in emails.sent[-1].text
    assert forgot.json()["dev_code"] == code

    assert reset(client, code).status_code == 200
    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {old_session}"}).status_code
        == 401
    )
    assert login(client).status_code == 401
    assert login(client, password="a different password").status_code == 200
    assert reset(client, code, "yet another password").status_code == 400


def test_a_wrong_reset_code_locks_after_five_attempts(
    client: TestClient, emails: FakeEmailSender
) -> None:
    signup_and_verify(client, emails)
    client.post("/v1/auth/forgot-password", json={"email": EMAIL})
    real = reset_code_from(emails)
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert reset(client, wrong).status_code == 400
    assert reset(client, real).status_code == 400
    assert login(client).status_code == 200

    client.post("/v1/auth/forgot-password", json={"email": EMAIL})
    assert reset(client, reset_code_from(emails)).status_code == 200


def test_an_expired_reset_code_is_refused(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    signup_and_verify(client, emails)
    client.post("/v1/auth/forgot-password", json={"email": EMAIL})
    token = session.scalars(select(MemberToken).where(MemberToken.kind == "reset")).one()
    token.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    assert reset(client, reset_code_from(emails)).status_code == 400


def test_a_verification_code_cannot_reset_a_password(
    client: TestClient, emails: FakeEmailSender
) -> None:
    client.post("/v1/auth/signup", json=payload())
    verification = code_from(emails)
    assert reset(client, verification).status_code == 400
    assert login(client).status_code == 403


def test_reset_does_not_reveal_which_accounts_exist(client: TestClient) -> None:
    response = reset(client, "123456", email="ghost@example.com")
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid or expired code."}


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef"])
def test_the_reset_code_must_have_six_digits(client: TestClient, code: str) -> None:
    assert reset(client, code).status_code == 422


def test_the_reset_code_is_only_returned_for_local_console_mail(
    app_factory: Any, emails: FakeEmailSender
) -> None:
    gateway = app_factory(EMAIL_BACKEND="gateway", GATEWAY_URL="http://gateway.test")
    gateway.app.state.email_sender = emails
    signup_and_verify(gateway, emails)
    response = gateway.post("/v1/auth/forgot-password", json={"email": EMAIL})
    assert response.json()["dev_code"] is None


def test_forgot_password_stays_silent_for_unknown_or_unverified_accounts(
    client: TestClient, emails: FakeEmailSender
) -> None:
    client.post("/v1/auth/signup", json=payload())
    emails.sent.clear()
    for address in ("ana@empresa.com.br", "ghost@example.com"):
        assert client.post("/v1/auth/forgot-password", json={"email": address}).status_code == 202
    assert emails.sent == []


def test_login_is_rate_limited(app_factory: Any, emails: FakeEmailSender) -> None:
    client = app_factory(AUTH_RATE_LIMIT_PER_MINUTE=3)
    statuses = [login(client, password="wrong password").status_code for _ in range(5)]
    assert statuses[:3] == [401, 401, 401]
    assert statuses[3:] == [429, 429]


def test_delete_account_requires_the_password(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    signup_and_verify(client, emails)
    token = login(client).json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert (
        client.post("/v1/auth/me/delete", json={"password": "wrong"}, headers=headers).status_code
        == 403
    )
    assert (
        client.post("/v1/auth/me/delete", json={"password": PASSWORD}, headers=headers).status_code
        == 200
    )
    assert session.scalar(select(func.count()).select_from(Member)) == 0
    assert session.scalar(select(func.count()).select_from(MemberSession)) == 0
    assert login(client).status_code == 401


def test_admin_lists_leads_by_score(
    client: TestClient, emails: FakeEmailSender, admin_headers: dict[str, str]
) -> None:
    signup_and_verify(client, emails)
    signup_and_verify(
        client,
        emails,
        email="aluno@ufrn.br",
        role="student",
        organization=None,
        segment=None,
        company_size=None,
        institution="UFRN",
        regions=[],
        plants=[],
        interests=["research"],
        marketing_opt_in=False,
    )

    assert client.get("/v1/admin/leads").status_code == 401
    everyone = client.get("/v1/admin/leads", headers=admin_headers).json()
    assert everyone["total"] == 2
    assert [item["email"] for item in everyone["items"]] == ["ana@empresa.com.br", "aluno@ufrn.br"]
    assert everyone["items"][0]["lead_tier"] == "hot"

    hot = client.get("/v1/admin/leads", params={"tier": "hot"}, headers=admin_headers).json()
    assert [item["email"] for item in hot["items"]] == ["ana@empresa.com.br"]


def test_a_member_turns_the_monthly_e_mail_on_and_off_without_a_confirmation(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    signup_and_verify(client, emails, marketing_opt_in=False)
    token = login(client).json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    before = len(emails.sent)

    on = client.put("/v1/auth/me/newsletter", json={"enabled": True}, headers=headers)
    assert on.status_code == 200
    assert on.json()["marketing_opt_in"] is True
    assert client.get("/v1/auth/me", headers=headers).json()["marketing_opt_in"] is True
    assert len(emails.sent) == before + 1  # the welcome; no confirmation to click
    assert "Newsletter ativada" in emails.sent[-1].subject

    again = client.put("/v1/auth/me/newsletter", json={"enabled": True}, headers=headers)
    assert again.json()["marketing_opt_in"] is True
    assert len(emails.sent) == before + 1  # already on: nothing more is sent

    off = client.put("/v1/auth/me/newsletter", json={"enabled": False}, headers=headers)
    assert off.json()["marketing_opt_in"] is False
    session.expire_all()
    assert session.scalars(select(Member.marketing_opt_in)).one() is False


def test_the_newsletter_preference_needs_a_session_and_a_boolean(
    client: TestClient, emails: FakeEmailSender
) -> None:
    assert client.put("/v1/auth/me/newsletter", json={"enabled": True}).status_code == 401
    signup_and_verify(client, emails)
    token = login(client).json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.put("/v1/auth/me/newsletter", json={}, headers=headers).status_code == 422
