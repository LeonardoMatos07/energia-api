from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dispatch import unsubscribe_token, verify_unsubscribe_token
from app.models import EmailDispatch, Member, MonthlyReport, NewsletterSubscriber
from tests.conftest import FakeEmailSender, make_settings

DISPATCHES = "/v1/admin/dispatches"
NOW = datetime(2026, 10, 5, tzinfo=UTC)
MONTH = date(2026, 9, 1)


def member(email: str, name: str, *, verified: bool = True, opt_in: bool = True) -> Member:
    return Member(
        email=email,
        name=name,
        password_hash="x",
        role="analyst",
        privacy_consent_at=NOW,
        email_verified_at=NOW if verified else None,
        marketing_opt_in=opt_in,
    )


def subscriber(email: str, *, confirmed: bool = True, unsubscribed: bool = False) -> Any:
    return NewsletterSubscriber(
        email=email,
        consented_at=NOW,
        confirmed_at=NOW if confirmed else None,
        unsubscribed_at=NOW if unsubscribed else None,
        unsubscribe_token_hash=f"hash-{email}",
    )


@pytest.fixture
def audience(session: Session) -> None:
    session.add_all(
        [
            member("ana@example.org", "Ana Souza"),
            member("both@example.org", "Beto Lima"),
            member("closed@example.org", "Caio", opt_in=False),
            member("pending@example.org", "Dani", verified=False),
            subscriber("both@example.org"),
            subscriber("fan@example.org"),
            subscriber("unconfirmed@example.org", confirmed=False),
            subscriber("gone@example.org", unsubscribed=True),
        ]
    )
    session.commit()


@pytest.fixture
def report(session: Session) -> MonthlyReport:
    row = MonthlyReport(
        month=MONTH,
        title="Corte <em>recorde</em> em setembro",
        slug="corte-recorde",
        summary="O corte voltou a subir.",
        content="x",
        published_at=NOW,
    )
    session.add(row)
    session.commit()
    return row


def start(client: TestClient, headers: dict[str, str], **body: Any) -> Any:
    return client.post(DISPATCHES, json=body, headers=headers)


def test_requires_admin(client: TestClient) -> None:
    assert client.post(DISPATCHES, json={"kind": "report"}).status_code == 401


@pytest.mark.usefixtures("audience", "report")
def test_the_report_goes_to_subscribers_and_opted_in_members_once_each(
    client: TestClient, admin_headers: dict[str, str], emails: FakeEmailSender
) -> None:
    response = start(client, admin_headers, kind="report", month="2026-09")

    assert response.status_code == 202
    assert sorted(m.to for m in emails.sent) == [
        "ana@example.org",
        "both@example.org",
        "fan@example.org",
    ]
    done = client.get(DISPATCHES, headers=admin_headers).json()["items"][0]
    assert (done["kind"], done["month"], done["status"]) == ("report", "2026-09", "done")
    assert (done["recipients"], done["sent"], done["failed"]) == (3, 3, 0)


@pytest.mark.usefixtures("audience", "report")
def test_each_message_is_personal_and_safe(
    client: TestClient, admin_headers: dict[str, str], emails: FakeEmailSender
) -> None:
    start(client, admin_headers, kind="report")
    by_to = {m.to: m for m in emails.sent}

    assert "Olá, Ana!" in by_to["ana@example.org"].text
    assert "Olá, Beto!" in by_to["both@example.org"].text
    assert "Olá, assinante!" in by_to["fan@example.org"].text
    fan = by_to["fan@example.org"]
    assert "/newsletter/descadastrar?token=" in fan.text
    assert "setembro de 2026" in fan.subject
    assert "<em>recorde</em>" not in fan.html
    assert "&lt;em&gt;recorde&lt;/em&gt;" in fan.html
    assert "http://web.test/relatorios/corte-recorde" in fan.html
    assert "/conta" in by_to["ana@example.org"].html


@pytest.mark.usefixtures("audience", "report")
def test_the_link_in_the_e_mail_unsubscribes_the_subscriber(
    client: TestClient,
    admin_headers: dict[str, str],
    emails: FakeEmailSender,
    session: Session,
) -> None:
    start(client, admin_headers, kind="report")
    fan = next(m for m in emails.sent if m.to == "fan@example.org")
    token = next(w for w in fan.text.split() if "token=" in w).split("token=")[1]

    assert client.get("/v1/newsletter/unsubscribe", params={"token": token}).status_code == 200
    session.expire_all()
    row = session.scalars(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == "fan@example.org")
    ).one()
    assert row.unsubscribed_at is not None


def test_a_forged_unsubscribe_token_is_refused(client: TestClient, session: Session) -> None:
    session.add(subscriber("fan@example.org"))
    session.commit()
    settings = make_settings()
    good = unsubscribe_token(settings, 1)

    assert verify_unsubscribe_token(settings, good) == 1
    assert verify_unsubscribe_token(settings, "1." + "0" * 64) is None
    assert verify_unsubscribe_token(settings, "x.y") is None
    assert verify_unsubscribe_token(make_settings(EVENT_HASH_SECRET="other"), good) is None
    assert (
        client.get("/v1/newsletter/unsubscribe", params={"token": "1." + "0" * 64}).status_code
        == 400
    )


@pytest.mark.usefixtures("audience", "report")
def test_the_analyses_go_only_to_opted_in_members(
    client: TestClient, admin_headers: dict[str, str], emails: FakeEmailSender
) -> None:
    response = start(client, admin_headers, kind="analysis", month="2026-09")

    assert response.status_code == 202
    assert sorted(m.to for m in emails.sent) == ["ana@example.org", "both@example.org"]
    assert "As análises de setembro de 2026" in emails.sent[0].subject
    assert "http://web.test/analises" in emails.sent[0].html


@pytest.mark.usefixtures("audience", "report")
def test_a_month_is_sent_once(
    client: TestClient, admin_headers: dict[str, str], emails: FakeEmailSender
) -> None:
    assert start(client, admin_headers, kind="report", month="2026-09").status_code == 202
    sent = len(emails.sent)

    again = start(client, admin_headers, kind="report", month="2026-09")

    assert again.status_code == 409
    assert len(emails.sent) == sent


@pytest.mark.usefixtures("audience")
def test_a_report_needs_to_be_published(
    client: TestClient, admin_headers: dict[str, str], emails: FakeEmailSender
) -> None:
    assert start(client, admin_headers, kind="report").status_code == 422
    assert start(client, admin_headers, kind="report", month="2026-09").status_code == 422
    assert emails.sent == []


@pytest.mark.usefixtures("audience", "report")
def test_a_send_that_fails_halfway_can_be_finished_without_repeating(
    client: TestClient,
    admin_headers: dict[str, str],
    emails: FakeEmailSender,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: dict[str, str] = {}
    original = emails.send

    def flaky(message: Any) -> None:
        keys.setdefault(message.to, message.idempotency_key)
        if message.to == "fan@example.org":
            raise RuntimeError("relay down")
        original(message)

    monkeypatch.setattr(emails, "send", flaky)
    start(client, admin_headers, kind="report", month="2026-09")
    row = session.scalars(select(EmailDispatch)).one()
    session.refresh(row)
    assert (row.status, row.sent, row.failed) == ("partial", 2, 1)

    assert start(client, admin_headers, kind="report", month="2026-09").status_code == 409
    monkeypatch.setattr(emails, "send", original)
    retried = start(client, admin_headers, kind="report", month="2026-09", retry=True)

    assert retried.status_code == 202
    session.refresh(row)
    assert (row.status, row.failed) == ("done", 0)
    assert len({k for k in keys.values()}) == 3
    assert all(k.startswith("energia-report-2026-09-") for k in keys.values())
