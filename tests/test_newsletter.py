from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AssinanteNewsletter
from app.routers.newsletter import hash_token
from tests.conftest import FakeEmailSender

EMAIL = "Pessoa@Exemplo.com.br"


def _subscribe(client: TestClient, email: str = EMAIL, **extra: object) -> None:
    r = client.post("/v1/newsletter", json={"email": email, "consentimento": True, **extra})
    assert r.status_code == 202
    assert set(r.json()) == {"mensagem"}


def _token(emails: FakeEmailSender, path: str, index: int = -1) -> str:
    text = emails.sent[index].text
    url = next(w for w in text.split() if w.startswith(f"http://web.test{path}?token="))
    return parse_qs(urlparse(url).query)["token"][0]


def _row(session: Session) -> AssinanteNewsletter:
    session.expire_all()
    row = session.scalars(select(AssinanteNewsletter)).one()
    return row


def test_full_flow(client: TestClient, emails: FakeEmailSender, session: Session) -> None:
    _subscribe(client, origem="home")
    assert len(emails.sent) == 1
    assert emails.sent[0].to == "pessoa@exemplo.com.br"
    confirm = _token(emails, "/newsletter/confirmar")
    unsubscribe = _token(emails, "/newsletter/descadastrar")

    row = _row(session)
    assert row.email == "pessoa@exemplo.com.br"
    assert row.origem == "home"
    assert row.confirmado_em is None
    # Only hashes are stored.
    assert row.token_confirmacao_hash == hash_token(confirm)
    assert row.token_descadastro_hash == hash_token(unsubscribe)
    assert confirm not in (row.token_confirmacao_hash, row.token_descadastro_hash)
    assert row.token_confirmacao_expira_em is not None

    r = client.get("/v1/newsletter/confirmar", params={"token": confirm})
    assert r.status_code == 200 and r.json() == {"confirmado": True}
    assert _row(session).confirmado_em is not None
    # Clicking the link twice is harmless.
    assert client.get("/v1/newsletter/confirmar", params={"token": confirm}).status_code == 200

    r = client.get("/v1/newsletter/descadastrar", params={"token": unsubscribe})
    assert r.status_code == 200 and r.json() == {"descadastrado": True}
    assert _row(session).descadastrado_em is not None
    r = client.get("/v1/newsletter/descadastrar", params={"token": unsubscribe})
    assert r.json() == {"descadastrado": True}

    # The old confirmation link no longer reactivates the subscription.
    assert client.get("/v1/newsletter/confirmar", params={"token": confirm}).status_code == 400


def test_resubscribe_active_is_silent(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    _subscribe(client)
    client.get(
        "/v1/newsletter/confirmar", params={"token": _token(emails, "/newsletter/confirmar")}
    )
    before = _row(session)
    _subscribe(client, email="pessoa@exemplo.com.br")
    assert len(emails.sent) == 1  # no new e-mail, same 202 answer
    after = _row(session)
    assert after.token_descadastro_hash == before.token_descadastro_hash
    assert after.confirmado_em == before.confirmado_em


def test_resubscribe_pending_resends(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    _subscribe(client)
    first = _token(emails, "/newsletter/confirmar")
    _subscribe(client)
    assert len(emails.sent) == 2
    second = _token(emails, "/newsletter/confirmar")
    assert first != second
    assert client.get("/v1/newsletter/confirmar", params={"token": first}).status_code == 400
    assert client.get("/v1/newsletter/confirmar", params={"token": second}).status_code == 200
    assert session.scalars(select(AssinanteNewsletter)).all().__len__() == 1


def test_resubscribe_after_unsubscribe(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    _subscribe(client)
    client.get(
        "/v1/newsletter/confirmar", params={"token": _token(emails, "/newsletter/confirmar")}
    )
    client.get(
        "/v1/newsletter/descadastrar", params={"token": _token(emails, "/newsletter/descadastrar")}
    )
    _subscribe(client)
    assert len(emails.sent) == 2
    row = _row(session)
    assert row.confirmado_em is None and row.descadastrado_em is None
    r = client.get(
        "/v1/newsletter/confirmar", params={"token": _token(emails, "/newsletter/confirmar")}
    )
    assert r.status_code == 200
    row = _row(session)
    assert row.confirmado_em is not None and row.descadastrado_em is None


def test_expired_token(client: TestClient, emails: FakeEmailSender, session: Session) -> None:
    _subscribe(client)
    row = _row(session)
    row.token_confirmacao_expira_em = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    r = client.get(
        "/v1/newsletter/confirmar", params={"token": _token(emails, "/newsletter/confirmar")}
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "Token inválido ou expirado."}


def test_expiry_is_48h(client: TestClient, session: Session) -> None:
    _subscribe(client)
    expires = _row(session).token_confirmacao_expira_em
    assert expires is not None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    delta = expires - datetime.now(UTC)
    assert timedelta(hours=47, minutes=59) < delta <= timedelta(hours=48)


def test_invalid_tokens(client: TestClient) -> None:
    for path in ("/v1/newsletter/confirmar", "/v1/newsletter/descadastrar"):
        r = client.get(path, params={"token": "x" * 43})
        assert r.status_code == 400
        assert client.get(path).status_code == 422  # token is required


def test_requires_consent_and_valid_email(client: TestClient, emails: FakeEmailSender) -> None:
    r = client.post("/v1/newsletter", json={"email": EMAIL, "consentimento": False})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)
    r = client.post("/v1/newsletter", json={"email": "nao-e-email", "consentimento": True})
    assert r.status_code == 422
    assert emails.sent == []


def test_rate_limit(client: TestClient) -> None:
    for i in range(5):
        _subscribe(client, email=f"p{i}@exemplo.com")
    r = client.post("/v1/newsletter", json={"email": "p9@exemplo.com", "consentimento": True})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60"
    assert isinstance(r.json()["detail"], str)


def test_rate_limit_uses_forwarded_ip_when_trusted(
    app_factory: Callable[..., TestClient],
) -> None:
    client = app_factory(CONFIAR_X_FORWARDED_FOR=True)
    for i in range(5):
        r = client.post(
            "/v1/newsletter",
            json={"email": f"p{i}@exemplo.com", "consentimento": True},
            headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2"},
        )
        assert r.status_code == 202
    r = client.post(
        "/v1/newsletter",
        json={"email": "outro@exemplo.com", "consentimento": True},
        headers={"X-Forwarded-For": "1.1.1.1, 3.3.3.3"},
    )
    assert r.status_code == 202  # different client IP (right-most entry)


def test_email_failure_still_202(app_factory: Callable[..., TestClient]) -> None:
    client = app_factory()

    class Broken:
        def send(self, message: object) -> None:
            raise RuntimeError("SES fora do ar")

    client.app.state.email_sender = Broken()  # type: ignore[attr-defined]
    _subscribe(client)
