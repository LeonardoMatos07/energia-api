from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NewsletterSubscriber
from app.routers.newsletter import purge_stale_subscribers
from app.security import hash_token
from tests.conftest import FakeEmailSender

EMAIL = "Person@Example.com"
CONFIRM_PATH = "/newsletter/confirmar"
UNSUBSCRIBE_PATH = "/newsletter/descadastrar"


def _subscribe(client: TestClient, email: str = EMAIL, **extra: object) -> None:
    r = client.post("/v1/newsletter", json={"email": email, "consent": True, **extra})
    assert r.status_code == 202
    assert set(r.json()) == {"message"}


def _token(emails: FakeEmailSender, path: str, index: int = -1) -> str:
    text = emails.sent[index].text
    url = next(w for w in text.split() if w.startswith(f"http://web.test{path}?token="))
    return parse_qs(urlparse(url).query)["token"][0]


def _row(session: Session) -> NewsletterSubscriber:
    session.expire_all()
    row = session.scalars(select(NewsletterSubscriber)).one()
    return row


def test_full_flow(client: TestClient, emails: FakeEmailSender, session: Session) -> None:
    _subscribe(client, source="home")
    assert len(emails.sent) == 1
    assert emails.sent[0].to == "person@example.com"
    confirm = _token(emails, CONFIRM_PATH)
    unsubscribe = _token(emails, UNSUBSCRIBE_PATH)

    row = _row(session)
    assert row.email == "person@example.com"
    assert row.signup_source == "home"
    assert row.confirmed_at is None
    assert row.confirmation_token_hash == hash_token(confirm)
    assert row.unsubscribe_token_hash == hash_token(unsubscribe)
    assert confirm not in (row.confirmation_token_hash, row.unsubscribe_token_hash)
    assert row.confirmation_token_expires_at is not None

    r = client.get("/v1/newsletter/confirm", params={"token": confirm})
    assert r.status_code == 200 and r.json() == {"confirmed": True}
    assert _row(session).confirmed_at is not None
    assert client.get("/v1/newsletter/confirm", params={"token": confirm}).status_code == 200

    r = client.get("/v1/newsletter/unsubscribe", params={"token": unsubscribe})
    assert r.status_code == 200 and r.json() == {"unsubscribed": True}
    assert _row(session).unsubscribed_at is not None
    r = client.get("/v1/newsletter/unsubscribe", params={"token": unsubscribe})
    assert r.json() == {"unsubscribed": True}

    assert client.get("/v1/newsletter/confirm", params={"token": confirm}).status_code == 400


def test_resubscribe_active_is_silent(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    _subscribe(client)
    client.get("/v1/newsletter/confirm", params={"token": _token(emails, CONFIRM_PATH)})
    before = _row(session)
    _subscribe(client, email="person@example.com")
    assert len(emails.sent) == 2  # the confirmation, then the welcome; nothing for the repeat
    after = _row(session)
    assert after.unsubscribe_token_hash == before.unsubscribe_token_hash
    assert after.confirmed_at == before.confirmed_at


def test_resubscribe_pending_resends(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    _subscribe(client)
    first = _token(emails, CONFIRM_PATH)
    _subscribe(client)
    assert len(emails.sent) == 2
    second = _token(emails, CONFIRM_PATH)
    assert first != second
    assert client.get("/v1/newsletter/confirm", params={"token": first}).status_code == 400
    assert client.get("/v1/newsletter/confirm", params={"token": second}).status_code == 200
    assert len(session.scalars(select(NewsletterSubscriber)).all()) == 1


def test_resubscribe_after_unsubscribe(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    _subscribe(client)
    client.get("/v1/newsletter/confirm", params={"token": _token(emails, CONFIRM_PATH)})
    client.get("/v1/newsletter/unsubscribe", params={"token": _token(emails, UNSUBSCRIBE_PATH)})
    _subscribe(client)
    assert len(emails.sent) == 3  # confirmation, welcome, then a new confirmation
    row = _row(session)
    assert row.confirmed_at is None and row.unsubscribed_at is None
    r = client.get("/v1/newsletter/confirm", params={"token": _token(emails, CONFIRM_PATH)})
    assert r.status_code == 200
    row = _row(session)
    assert row.confirmed_at is not None and row.unsubscribed_at is None


def test_expired_token(client: TestClient, emails: FakeEmailSender, session: Session) -> None:
    _subscribe(client)
    row = _row(session)
    row.confirmation_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    r = client.get("/v1/newsletter/confirm", params={"token": _token(emails, CONFIRM_PATH)})
    assert r.status_code == 400
    assert r.json() == {"detail": "Invalid or expired token."}


def test_purges_expired_pending_and_old_unsubscribed(session: Session) -> None:
    now = datetime.now(UTC)

    def add(email: str, **values: object) -> None:
        defaults: dict[str, object] = {
            "consented_at": now - timedelta(days=60),
            "unsubscribe_token_hash": hash_token(email),
        }
        session.add(NewsletterSubscriber(email=email, **(defaults | values)))

    add("expired-pending@x.com", confirmation_token_expires_at=now - timedelta(minutes=1))
    add("valid-pending@x.com", confirmation_token_expires_at=now + timedelta(hours=1))
    add("active@x.com", confirmed_at=now - timedelta(days=59))
    add("left-31-days-ago@x.com", confirmed_at=now, unsubscribed_at=now - timedelta(days=31))
    add("left-yesterday@x.com", confirmed_at=now, unsubscribed_at=now - timedelta(days=1))
    session.commit()

    purge_stale_subscribers(session, now)
    session.commit()

    kept = set(session.scalars(select(NewsletterSubscriber.email)))
    assert kept == {"valid-pending@x.com", "active@x.com", "left-yesterday@x.com"}


def test_subscribe_runs_the_purge(client: TestClient, session: Session) -> None:
    _subscribe(client, "old@x.com")
    row = _row(session)
    row.confirmation_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()

    _subscribe(client, "new@x.com")

    session.expire_all()
    assert set(session.scalars(select(NewsletterSubscriber.email))) == {"new@x.com"}


def test_expiry_is_48h(client: TestClient, session: Session) -> None:
    _subscribe(client)
    expires = _row(session).confirmation_token_expires_at
    assert expires is not None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    delta = expires - datetime.now(UTC)
    assert timedelta(hours=47, minutes=59) < delta <= timedelta(hours=48)


def test_invalid_tokens(client: TestClient) -> None:
    for path in ("/v1/newsletter/confirm", "/v1/newsletter/unsubscribe"):
        r = client.get(path, params={"token": "x" * 43})
        assert r.status_code == 400
        assert client.get(path).status_code == 422


def test_requires_consent_and_valid_email(client: TestClient, emails: FakeEmailSender) -> None:
    r = client.post("/v1/newsletter", json={"email": EMAIL, "consent": False})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)
    r = client.post("/v1/newsletter", json={"email": "not-an-email", "consent": True})
    assert r.status_code == 422
    assert emails.sent == []


def test_rate_limit(client: TestClient) -> None:
    for i in range(5):
        _subscribe(client, email=f"p{i}@example.com")
    r = client.post("/v1/newsletter", json={"email": "p9@example.com", "consent": True})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60"
    assert isinstance(r.json()["detail"], str)


def test_rate_limit_uses_forwarded_ip_when_trusted(
    app_factory: Callable[..., TestClient],
) -> None:
    client = app_factory(TRUST_X_FORWARDED_FOR=True)
    for i in range(5):
        r = client.post(
            "/v1/newsletter",
            json={"email": f"p{i}@example.com", "consent": True},
            headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2"},
        )
        assert r.status_code == 202
    r = client.post(
        "/v1/newsletter",
        json={"email": "other@example.com", "consent": True},
        headers={"X-Forwarded-For": "1.1.1.1, 3.3.3.3"},
    )
    assert r.status_code == 202


WEB_SECRET = "energia-web-secret"


def _post_from_web(client: TestClient, i: int, headers: dict[str, str]) -> int:
    r = client.post(
        "/v1/newsletter",
        json={"email": f"p{i}@example.com", "consent": True},
        headers=headers,
    )
    status: int = r.status_code
    return status


def test_rate_limit_uses_client_ip_header_with_web_secret(
    app_factory: Callable[..., TestClient],
) -> None:
    client = app_factory(WEB_API_SECRET=WEB_SECRET)
    visitor = {"X-Web-Api-Secret": WEB_SECRET, "X-Client-IP": "200.1.1.1"}
    for i in range(5):
        assert _post_from_web(client, i, visitor) == 202
    assert _post_from_web(client, 5, visitor) == 429
    other = {"X-Web-Api-Secret": WEB_SECRET, "X-Client-IP": "2804:14c::1"}
    assert _post_from_web(client, 6, other) == 202


@pytest.mark.parametrize(
    ("settings", "headers"),
    [
        ({"WEB_API_SECRET": WEB_SECRET}, {"X-Web-Api-Secret": "wrong"}),
        ({"WEB_API_SECRET": WEB_SECRET}, {}),
        ({"WEB_API_SECRET": ""}, {"X-Web-Api-Secret": ""}),
        ({"WEB_API_SECRET": WEB_SECRET}, {"X-Web-Api-Secret": WEB_SECRET, "X-Client-IP": "x"}),
    ],
)
def test_client_ip_header_ignored_without_valid_secret(
    app_factory: Callable[..., TestClient], settings: dict[str, str], headers: dict[str, str]
) -> None:
    client = app_factory(**settings)
    for i in range(5):
        spoofed = {"X-Client-IP": f"10.0.0.{i}", **headers}
        assert _post_from_web(client, i, spoofed) == 202
    assert _post_from_web(client, 5, {"X-Client-IP": "10.0.0.99", **headers}) == 429


def test_client_ip_header_takes_precedence_over_forwarded_for(
    app_factory: Callable[..., TestClient],
) -> None:
    client = app_factory(WEB_API_SECRET=WEB_SECRET, TRUST_X_FORWARDED_FOR=True)
    for i in range(5):
        headers = {
            "X-Web-Api-Secret": WEB_SECRET,
            "X-Client-IP": f"200.1.1.{i}",
            "X-Forwarded-For": "1.1.1.1, 2.2.2.2",
        }
        assert _post_from_web(client, i, headers) == 202
    assert _post_from_web(client, 5, {"X-Forwarded-For": "1.1.1.1, 2.2.2.2"}) == 202


def test_email_failure_still_202(app_factory: Callable[..., TestClient]) -> None:
    client = app_factory()

    class Broken:
        def send(self, message: object) -> None:
            raise RuntimeError("SES is down")

    cast(FastAPI, client.app).state.email_sender = Broken()
    _subscribe(client)
