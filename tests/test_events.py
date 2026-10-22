from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.events import classify_path, detect_device, hash_ip
from app.models import Member, UsageEvent
from tests.conftest import FakeEmailSender, make_settings
from tests.test_auth import PASSWORD, login, payload, signup_and_verify

WEB_SECRET = "web-shared-secret"


def web_headers(**extra: str) -> dict[str, str]:
    return {"x-web-api-secret": WEB_SECRET, "x-client-ip": "203.0.113.7", **extra}


def events(session: Session, **filters: Any) -> list[UsageEvent]:
    stmt = select(UsageEvent).order_by(UsageEvent.id)
    for key, value in filters.items():
        stmt = stmt.where(getattr(UsageEvent, key) == value)
    return list(session.scalars(stmt))


def test_classify_path_maps_pages_to_sections_and_entities() -> None:
    state = classify_path("/estados/ba")
    assert (state.section, state.entity_type, state.entity_id) == ("estados", "state", "BA")

    plant = classify_path("/usinas/sao-fernando-4-rnesf4")
    assert (plant.section, plant.entity_type, plant.entity_id) == (
        "usinas",
        "plant",
        "sao-fernando-4-rnesf4",
    )

    report = classify_path("/relatorios/curtailment-2026-09")
    assert (report.section, report.entity_type) == ("relatorios", "report")

    dashboard = classify_path("/painel", {"de": "2026-01", "ate": "2026-09", "x": "ignored"})
    assert dashboard.section == "painel"
    assert dashboard.properties == {"de": "2026-01", "ate": "2026-09"}

    assert classify_path("/").section == "inicio"
    assert classify_path("/nada-aqui").section == "outros"


def test_classify_path_lists_the_compared_entities() -> None:
    found = classify_path("/comparar", "itens=e%3ABA%2Ce%3ARN&metricas=perda&grafico=barras")
    assert found.section == "comparar"
    assert found.entity_type == "comparison"
    assert found.properties["entities"] == ["e:BA", "e:RN"]
    assert found.properties["metricas"] == "perda"


def test_classify_path_never_keeps_tokens() -> None:
    found = classify_path("/verificar-email", {"token": "secret", "proximo": "/painel"})
    assert found.section == "cadastro"
    assert found.properties == {}


@pytest.mark.parametrize(
    ("agent", "device", "bot"),
    [
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17) Mobile Safari", "mobile", False),
        ("Mozilla/5.0 (iPad; CPU OS 17) Safari", "tablet", False),
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/120", "desktop", False),
        ("Googlebot/2.1 (+http://www.google.com/bot.html)", "bot", True),
        ("HeadlessChrome/120", "bot", True),
        (None, None, False),
    ],
)
def test_detect_device(agent: str | None, device: str | None, bot: bool) -> None:
    assert detect_device(agent) == (device, bot)


def test_ip_hash_is_stable_keyed_and_not_the_ip() -> None:
    first = hash_ip("203.0.113.7", "secret-a")
    assert first == hash_ip("203.0.113.7", "secret-a")
    assert first != hash_ip("203.0.113.7", "secret-b")
    assert first is not None and "203.0.113.7" not in first
    assert hash_ip(None, "secret-a") is None


def test_production_requires_the_event_secrets() -> None:
    with pytest.raises(ValidationError):
        make_settings(ENVIRONMENT="prod")


def test_ingest_stores_classified_page_and_interaction_events(
    client: TestClient, session: Session
) -> None:
    response = client.post(
        "/v1/events",
        json={
            "visitor_id": "visitor-1",
            "session_key": "session-1",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120",
            "referrer": "https://www.google.com/",
            "events": [
                {"category": "page", "action": "page.view", "path": "/estados/ba"},
                {
                    "category": "interaction",
                    "action": "chart.kind_change",
                    "path": "/painel",
                    "properties": {"kind": "line"},
                },
            ],
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": 2}

    page, interaction = events(session)
    assert (page.section, page.entity_type, page.entity_id) == ("estados", "state", "BA")
    assert page.visitor_id == "visitor-1"
    assert page.session_key == "session-1"
    assert page.device == "desktop"
    assert page.is_bot is False
    assert page.referrer == "https://www.google.com/"
    assert interaction.section == "painel"
    assert interaction.properties == {"kind": "line"}
    assert page.request_id == interaction.request_id


def test_ingest_attaches_the_logged_member(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    signup_and_verify(client, emails)
    token = login(client).json()["session_token"]
    client.post(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
        json={"events": [{"category": "page", "action": "page.view", "path": "/painel"}]},
    )
    stored = events(session, action="page.view")[0]
    member = session.scalars(select(Member)).one()
    assert stored.member_id == member.id
    assert stored.member_role == "professional"
    assert stored.member_segment == "generator"


@pytest.mark.parametrize(
    "body",
    [
        {"events": []},
        {"events": [{"action": "Bad Action!"}]},
        {"events": [{"action": "ok", "category": "auth"}]},
        {"events": [{"action": "ok"}] * 51},
    ],
)
def test_ingest_validates_the_batch(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/v1/events", json=body).status_code == 422


def test_ingest_requires_the_shared_secret_when_configured(
    app_factory: Any, session: Session
) -> None:
    client = app_factory(WEB_API_SECRET=WEB_SECRET)
    body = {"events": [{"category": "page", "action": "page.view", "path": "/"}]}
    assert client.post("/v1/events", json=body).status_code == 401
    assert (
        client.post("/v1/events", json=body, headers={"x-web-api-secret": "wrong"}).status_code
        == 401
    )
    assert client.post("/v1/events", json=body, headers=web_headers()).status_code == 202
    stored = events(session)[0]
    assert stored.ip_hash is not None
    assert "203.0.113.7" not in stored.ip_hash


def test_trusted_headers_supply_the_visitor_agent_and_ip(
    app_factory: Any, session: Session
) -> None:
    client = app_factory(WEB_API_SECRET=WEB_SECRET)
    client.post(
        "/v1/events",
        json={"events": [{"category": "page", "action": "page.view", "path": "/"}]},
        headers=web_headers(**{"x-visitor-id": "from-header", "x-user-agent": "Googlebot/2.1"}),
    )
    stored = events(session)[0]
    assert stored.is_bot is True
    assert stored.device == "bot"


def test_auth_flow_is_audited(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    client.post("/v1/auth/signup", json=payload())
    login(client, password="wrong password!!")
    from tests.test_auth import verify

    verify(client, emails)
    token = login(client).json()["session_token"]
    client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})

    actions = [event.action for event in events(session, category="auth")]
    assert actions == [
        "auth.signup",
        "auth.login_failed",
        "auth.verify",
        "auth.login",
        "auth.logout",
    ]
    signup = events(session, action="auth.signup")[0]
    assert signup.properties["lead_tier"] == "hot"
    assert "email" not in signup.properties
    assert "password" not in str(signup.properties)
    assert events(session, action="auth.login_failed")[0].member_id is not None


def test_deleting_an_account_anonymises_its_events(
    client: TestClient, emails: FakeEmailSender, session: Session
) -> None:
    signup_and_verify(client, emails)
    token = login(client).json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        "/v1/events",
        headers=headers,
        json={
            "visitor_id": "visitor-9",
            "user_agent": "Mozilla/5.0 Chrome",
            "events": [{"category": "page", "action": "page.view", "path": "/painel"}],
        },
    )
    assert (
        client.post("/v1/auth/me/delete", json={"password": PASSWORD}, headers=headers).status_code
        == 200
    )

    session.expire_all()
    remaining = events(session)
    assert remaining
    assert all(event.member_id is None for event in remaining)
    assert all(event.visitor_id is None and event.user_agent is None for event in remaining)
    assert "auth.account_deleted" in [event.action for event in remaining]


def test_newsletter_and_admin_actions_are_audited(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    client.post(
        "/v1/newsletter", json={"email": "reader@example.org", "consent": True, "source": "landing"}
    )
    client.post(
        "/v1/admin/reports",
        headers=admin_headers,
        json={"month": "2026-09", "title": "September", "content": "# Body", "publish": True},
    )
    subscribed = events(session, action="newsletter.subscribe")[0]
    assert subscribed.properties == {"source": "landing"}
    saved = events(session, action="admin.report_saved")[0]
    assert saved.properties["slug"] == "september"
    assert saved.properties["published"] is True


def seed_usage(session: Session) -> Member:
    member = Member(
        email="ana@empresa.com.br",
        password_hash="x",
        name="Ana Souza",
        role="professional",
        organization="Parque SA",
        segment="generator",
        privacy_consent_at=datetime.now(UTC),
        email_verified_at=datetime.now(UTC),
        lead_score=100,
        lead_tier="hot",
    )
    other = Member(
        email="bia@univ.br",
        password_hash="x",
        name="Bia",
        role="student",
        institution="UFRN",
        privacy_consent_at=datetime.now(UTC),
        email_verified_at=datetime.now(UTC),
        lead_score=10,
        lead_tier="cold",
    )
    session.add_all([member, other])
    session.flush()
    now = datetime.now(UTC)

    def add(
        action: str,
        *,
        category: str = "page",
        section: str | None = None,
        entity: tuple[str, str] | None = None,
        who: Member | None = None,
        visitor: str | None = None,
        days_ago: float = 0.1,
        bot: bool = False,
        **properties: Any,
    ) -> None:
        session.add(
            UsageEvent(
                occurred_at=now - timedelta(days=days_ago),
                category=category,
                action=action,
                section=section,
                entity_type=entity[0] if entity else None,
                entity_id=entity[1] if entity else None,
                properties=properties,
                member_id=who.id if who else None,
                member_role=who.role if who else None,
                member_segment=who.segment if who else None,
                visitor_id=visitor,
                session_key=f"s-{visitor or (who.id if who else 'anon')}",
                is_bot=bot,
            )
        )

    add("page.view", section="inicio", visitor="v1")
    add("page.view", section="inicio", visitor="v2")
    add("page.view", section="cadastro", visitor="v1")
    add("auth.signup", category="auth", who=member)
    add("auth.verify", category="auth", who=member)
    add("auth.login", category="auth", who=member, days_ago=2)
    add("page.view", section="estados", entity=("state", "RN"), who=member, days_ago=2)
    add("page.view", section="estados", entity=("state", "RN"), who=other)
    add("page.view", section="estados", entity=("state", "BA"), who=member)
    add("page.view", section="usinas", entity=("plant", "cumaru-i"), who=member)
    add("page.view", section="comparar", who=member, entities=["e:BA", "e:RN"])
    add(
        "compare.configure",
        category="interaction",
        section="comparar",
        who=member,
        entities=["e:RN", "s:NE"],
    )
    add("search.plants", category="interaction", section="usinas", who=member, q="Cumaru")
    add("search.plants", category="interaction", section="usinas", who=other, q="cumaru ")
    add("chart.kind_change", category="interaction", section="painel", who=member)
    add("page.view", section="painel", visitor="bot-1", bot=True)
    session.commit()
    return member


def test_usage_summary_aggregates_the_activity(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    member = seed_usage(session)
    body = client.get("/v1/admin/usage/summary", params={"days": 7}, headers=admin_headers).json()

    assert body["totals"]["members_active"] == 2
    assert body["totals"]["visitors"] == 2
    assert body["totals"]["page_views"] == 8
    assert body["totals"]["new_members"] == 2

    sections = {item["section"]: item for item in body["sections"]}
    assert sections["estados"]["views"] == 3
    assert sections["estados"]["members"] == 2
    assert "painel" not in sections

    states = [item for item in body["entities"] if item["entity_type"] == "state"]
    assert [(item["entity_id"], item["views"]) for item in states] == [("RN", 2), ("BA", 1)]

    assert body["searches"] == [{"name": "cumaru", "count": 2}]
    assert body["compared"][0] == {"name": "e:RN", "count": 2}
    assert {item["name"] for item in body["compared"]} == {"e:BA", "e:RN", "s:NE"}
    assert {"name": "chart.kind_change", "count": 1} in body["interactions"]

    funnel = {step["step"]: step["count"] for step in body["funnel"]}
    assert funnel["Visitaram a página inicial"] == 2
    assert funnel["Abriram o cadastro"] == 1
    assert funnel["Enviaram o cadastro"] == 1
    assert funnel["Confirmaram o e-mail"] == 1
    assert funnel["Entraram na conta"] == 1
    assert funnel["Voltaram em 2+ dias"] == 1

    roles = {(item["role"], item["segment"]): item["members"] for item in body["roles"]}
    assert roles[("professional", "generator")] == 1
    assert roles[("student", None)] == 1

    assert [lead["email"] for lead in body["active_leads"]] == ["ana@empresa.com.br"]
    assert body["active_leads"][0]["member_id"] == str(member.id)
    assert body["daily"]

    with_bots = client.get(
        "/v1/admin/usage/summary", params={"days": 7, "include_bots": True}, headers=admin_headers
    ).json()
    assert with_bots["totals"]["page_views"] == 9


def test_usage_endpoints_require_admin(client: TestClient) -> None:
    for path in ("summary", "events", "events.csv"):
        assert client.get(f"/v1/admin/usage/{path}").status_code == 401
    assert client.post("/v1/admin/usage/purge").status_code == 401


def test_usage_events_listing_and_filters(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    member = seed_usage(session)
    everything = client.get("/v1/admin/usage/events", headers=admin_headers).json()
    assert everything["total"] == 15

    states = client.get(
        "/v1/admin/usage/events", params={"section": "estados"}, headers=admin_headers
    ).json()
    assert states["total"] == 3

    mine = client.get(
        "/v1/admin/usage/events", params={"member_id": str(member.id)}, headers=admin_headers
    ).json()
    assert {item["member_id"] for item in mine["items"]} == {str(member.id)}
    assert mine["items"][0]["occurred_at"] >= mine["items"][-1]["occurred_at"]


def test_usage_csv_export(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    seed_usage(session)
    response = client.get("/v1/admin/usage/events.csv", params={"days": 7}, headers=admin_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("id,occurred_at,category,action,section")
    assert len(lines) == 17
    assert any("ana@empresa.com.br" in line for line in lines[1:])


def test_member_timeline(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    member = seed_usage(session)
    body = client.get(f"/v1/admin/usage/members/{member.id}", headers=admin_headers).json()
    assert body["member"]["email"] == "ana@empresa.com.br"
    assert body["total"] == 10
    assert body["events"][0]["action"] != ""
    missing = client.get(
        "/v1/admin/usage/members/00000000-0000-0000-0000-000000000000", headers=admin_headers
    )
    assert missing.status_code == 404


def test_purge_removes_only_old_events(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    seed_usage(session)
    session.add(
        UsageEvent(
            occurred_at=datetime.now(UTC) - timedelta(days=800),
            category="page",
            action="page.view",
            properties={},
            is_bot=False,
        )
    )
    session.commit()
    before = session.scalar(select(func.count()).select_from(UsageEvent))

    response = client.post("/v1/admin/usage/purge", params={"days": 730}, headers=admin_headers)
    assert response.json() == {"deleted": 1, "older_than_days": 730}
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(UsageEvent)) == (before or 0) - 1


def test_burst_of_identical_page_views_is_stored_once(client: TestClient, session: Session) -> None:
    def view(query: dict[str, str]) -> None:
        client.post(
            "/v1/events",
            json={
                "session_key": "burst",
                "events": [
                    {"category": "page", "action": "page.view", "path": "/painel", "query": query}
                ],
            },
        )

    view({"de": "2026-01", "ate": "2026-09"})
    view({"de": "2026-01", "ate": "2026-09"})
    view({"de": "2026-06", "ate": "2026-09"})
    stored = events(session, action="page.view")
    assert [event.properties for event in stored] == [
        {"de": "2026-01", "ate": "2026-09"},
        {"de": "2026-06", "ate": "2026-09"},
    ]
