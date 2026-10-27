import json

import httpx
import pytest
from pydantic import ValidationError

from app.email import EmailMessage, GatewayEmailSender, make_email_sender
from tests.conftest import make_settings

MESSAGE = EmailMessage(
    to="ana@example.org", subject="Seu código: 123456", text="Código 123456", html="<p>x</p>"
)


def sender(handler: httpx.MockTransport) -> GatewayEmailSender:
    return GatewayEmailSender("http://gateway.test/", "secret-key", 5.0, transport=handler)


def test_gateway_sender_posts_the_message_with_the_key() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(202, json={"id": "abc", "status": "accepted"})

    sender(httpx.MockTransport(handler)).send(MESSAGE)

    request = calls[0]
    assert str(request.url) == "http://gateway.test/v1/messages"
    assert request.headers["x-api-key"] == "secret-key"
    assert request.headers["idempotency-key"].startswith("energia-")
    assert json.loads(request.content) == {
        "channel": "email",
        "to": "ana@example.org",
        "subject": "Seu código: 123456",
        "body": "Código 123456",
    }


def test_each_message_gets_its_own_idempotency_key() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        return httpx.Response(202, json={})

    client = sender(httpx.MockTransport(handler))
    client.send(MESSAGE)
    client.send(MESSAGE)
    assert keys[0] != keys[1]


@pytest.mark.parametrize("status", [400, 401, 502, 504])
def test_gateway_sender_raises_when_the_gateway_refuses(status: int) -> None:
    client = sender(httpx.MockTransport(lambda request: httpx.Response(status, json={})))
    with pytest.raises(httpx.HTTPStatusError):
        client.send(MESSAGE)


def test_the_gateway_backend_is_selected_and_needs_a_url() -> None:
    settings = make_settings(EMAIL_BACKEND="gateway", GATEWAY_URL="http://gateway.test")
    assert isinstance(make_email_sender(settings), GatewayEmailSender)
    with pytest.raises(ValidationError):
        make_settings(EMAIL_BACKEND="gateway")
