import json

import httpx
import pytest
from pydantic import ValidationError

from app.email import EmailMessage, GatewayEmailSender, make_email_sender, template_message
from app.email_templates import build
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
        "html": "<p>x</p>",
        "metadata": {"product": "energia"},
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


def test_a_template_travels_to_relay_with_its_variables_and_key() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(202, json={})

    message = template_message(
        make_settings(),
        "verification-code",
        "ana@example.org",
        {"name": "Ana", "code": "123456"},
        fixed={"minutes": 30},
        idempotency_key="energia-fixed-key-1",
    )
    sender(httpx.MockTransport(handler)).send(message)

    request = calls[0]
    body = json.loads(request.content)
    assert request.headers["idempotency-key"] == "energia-fixed-key-1"
    assert body["variables"] == {"name": "Ana", "code": "123456"}
    assert body["metadata"] == {"product": "energia", "template": "verification-code"}
    assert "{{code}}" in body["html"]
    assert "{{name}}" in body["body"]
    assert "[[" not in body["html"] + body["body"] + body["subject"]
    assert "30 minutos" in body["body"]


@pytest.mark.parametrize(
    ("name", "variables"),
    [
        ("verification-code", {"name": "Ana", "code": "123456"}),
        ("reset-code", {"name": "Ana", "code": "123456"}),
        ("existing-account", {"name": "Ana"}),
        ("report", {"name": "Ana", "unsubscribe_url": "https://x.test/u?a=1&b=2"}),
        ("analysis", {"name": "Ana"}),
        (
            "newsletter-confirmation",
            {"confirm_url": "https://x.test/c?t=1&u=2", "unsubscribe_url": "https://x.test/u"},
        ),
    ],
)
def test_every_template_renders_completely_with_only_its_variables(
    name: str, variables: dict[str, str]
) -> None:
    fixed: dict[str, str | int] = {
        "minutes": 30,
        "month_label": "setembro de 2026",
        "report_title": "Corte em alta",
        "report_summary": "Resumo.",
        "report_url": "https://x.test/relatorios/a",
    }
    message = template_message(make_settings(), name, "ana@example.org", variables, fixed=fixed)
    rendered = message.rendered()

    for part in (rendered.subject, rendered.text, rendered.html):
        assert "{{" not in part
        assert "[[" not in part
    assert "<html" in rendered.html
    assert "Spillway" in rendered.html
    assert set(build(name, "https://x.test", **fixed).variables) == set(variables)


def test_the_name_is_escaped_in_the_html_and_not_in_the_text() -> None:
    message = template_message(
        make_settings(), "existing-account", "a@example.org", {"name": "<b>Ana</b>"}
    ).rendered()
    assert "<b>Ana</b>" not in message.html
    assert "&lt;b&gt;Ana&lt;/b&gt;" in message.html
    assert "<b>Ana</b>" in message.text


def test_rendering_without_a_variable_fails_loudly() -> None:
    message = template_message(
        make_settings(), "reset-code", "a@example.org", {"name": "Ana"}, fixed={"minutes": 30}
    )
    with pytest.raises(KeyError, match="code"):
        message.rendered()


def test_a_long_report_title_is_shortened_in_the_subject_only() -> None:
    title = "T" * 300
    template = build(
        "report",
        "https://x.test",
        report_title=title,
        month_label="maio de 2026",
        report_summary="x",
        report_url="https://x.test/r",
    )
    assert len(template.subject) < 200
    assert title in template.text
