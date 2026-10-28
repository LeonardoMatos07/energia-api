from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.dispatch import Recipient, build_messages, content_blocks
from app.exporting.kpis import month_kpis, shift_month
from app.models import MonthlyReport, NewsletterSubscriber
from tests.conftest import FakeEmailSender, make_settings
from tests.test_auth import login, signup_and_verify

NOW = datetime(2026, 10, 5, tzinfo=UTC)
SEP = date(2026, 9, 1)
REPORT = """## O mês em números

- **Energia cortada:** 1 GWh

## Onde

Texto do relatório.

```chart
{"type":"bar","title":"Tendência","unit":"GWh","labels":["ago/26","set/26"],"series":[{"name":"x","values":[1.0,2.0]}]}
```
"""


@pytest.fixture
def report(session: Session) -> MonthlyReport:
    row = MonthlyReport(
        month=SEP,
        title="Curtailment em setembro de 2026",
        slug="curtailment-2026-09",
        summary="Resumo do mês.",
        content=REPORT,
        published_at=NOW,
    )
    session.add(row)
    session.commit()
    return row


def enable(client: TestClient, emails: FakeEmailSender) -> dict[str, str]:
    signup_and_verify(client, emails, marketing_opt_in=False)
    token = login(client).json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    emails.sent.clear()
    assert (
        client.put("/v1/auth/me/newsletter", json={"enabled": True}, headers=headers).status_code
        == 200
    )
    return headers


def test_enabling_the_newsletter_sends_the_welcome_then_the_latest_report(
    client: TestClient, emails: FakeEmailSender, report: MonthlyReport, seed: None
) -> None:
    enable(client, emails)

    welcome, latest = emails.sent
    assert welcome.to == latest.to == "ana@empresa.com.br"
    assert "Newsletter ativada" in welcome.subject
    assert "Parabéns, Ana!" in welcome.text
    assert "relatório geral" in welcome.text
    assert (
        "Em poucos minutos você recebe o relatório mais recente, o de setembro de 2026"
        in welcome.text
    )
    assert "http://web.test/relatorios" in welcome.html
    assert "setembro de 2026" in latest.subject
    assert "Texto do relatório." in latest.html


def test_the_welcome_says_what_comes_next_when_no_report_exists_yet(
    client: TestClient, emails: FakeEmailSender
) -> None:
    enable(client, emails)

    assert len(emails.sent) == 1
    assert "O primeiro relatório chega assim que os próximos dados do ONS" in emails.sent[0].text


def test_the_report_e_mail_opens_with_the_month_in_numbers_and_has_no_repeated_list(
    client: TestClient, emails: FakeEmailSender, report: MonthlyReport, seed: None
) -> None:
    enable(client, emails)
    latest = emails.sent[-1]

    assert "Energia cortada" in latest.html
    assert "% de corte" in latest.html
    assert "Perda estimada" in latest.html
    assert "Usinas afetadas" in latest.html
    assert "vs. mês anterior" in latest.html
    assert latest.html.count("Energia cortada") == 1  # the report's own bullet list is dropped
    assert "O mês em números:" in latest.text
    assert "Tendência" in latest.html  # the chart is there
    assert len(latest.text) < 4_000


def test_the_welcome_report_is_the_same_message_the_monthly_send_builds(
    session: Session, report: MonthlyReport, seed: None
) -> None:
    settings = make_settings()
    recipient = Recipient("ana@example.org", "Ana", "http://web.test/newsletter/descadastrar")
    blocks = content_blocks(session, settings, "report", report.month, report)

    first = build_messages(settings, "report", report.month, [recipient], report, blocks)
    again = build_messages(settings, "report", report.month, [recipient], report, blocks)

    assert first[0].idempotency_key == again[0].idempotency_key
    assert first[0].idempotency_key is not None
    assert first[0].idempotency_key.startswith("energia-report-2026-09-")


def test_turning_it_on_again_later_does_not_resend(
    client: TestClient, emails: FakeEmailSender, report: MonthlyReport, seed: None
) -> None:
    headers = enable(client, emails)
    client.put("/v1/auth/me/newsletter", json={"enabled": False}, headers=headers)
    sent = len(emails.sent)
    client.put("/v1/auth/me/newsletter", json={"enabled": True}, headers=headers)

    # The same idempotency keys go to Relay, which answers with the original messages.
    keys = [m.idempotency_key for m in emails.sent]
    assert keys[:2] == keys[sent : sent + 2]


def test_confirming_the_newsletter_by_e_mail_also_sends_the_welcome_and_the_report(
    client: TestClient, emails: FakeEmailSender, session: Session, report: MonthlyReport, seed: None
) -> None:
    client.post("/v1/newsletter", json={"email": "fan@example.org", "consent": True})
    confirm_url = next(
        w for w in emails.sent[0].text.split() if "/newsletter/confirmar?token=" in w
    )
    token = confirm_url.split("token=")[1]
    emails.sent.clear()

    assert client.get("/v1/newsletter/confirm", params={"token": token}).status_code == 200

    welcome, latest = emails.sent
    assert "Parabéns, assinante!" in welcome.text
    assert "/newsletter/descadastrar?token=" in welcome.text
    assert "setembro de 2026" in latest.subject
    # asking again for the same link is harmless: nothing more is sent
    emails.sent.clear()
    client.get("/v1/newsletter/confirm", params={"token": token})
    assert emails.sent == []
    assert session.query(NewsletterSubscriber).count() == 1


def test_month_arithmetic_and_the_tiles_without_data(session: Session) -> None:
    assert shift_month(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert shift_month(date(2026, 9, 1), -12) == date(2025, 9, 1)
    assert month_kpis(session, date(2019, 1, 1)) == []


@pytest.mark.usefixtures("seed")
def test_the_tiles_compare_with_the_previous_month(session: Session) -> None:
    tiles = {t.label: t for t in month_kpis(session, SEP)}

    assert set(tiles) == {"Energia cortada", "% de corte", "Perda estimada", "Usinas afetadas"}
    assert tiles["Energia cortada"].value.endswith("GWh")
    assert any("vs. mês anterior" in note for note, _ in tiles["Energia cortada"].notes)
