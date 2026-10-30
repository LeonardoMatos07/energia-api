from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import NewsItem, NewsSource, RegulatoryAct

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def news(session: Session) -> dict[str, NewsItem]:
    aneel = NewsSource(
        slug="aneel",
        publisher_name="Agência Nacional de Energia Elétrica (ANEEL)",
        homepage_url="https://www.gov.br/aneel",
        fetch_method="api",
        license_code="CC-BY-ND-3.0",
        license_url="https://creativecommons.org/licenses/by-nd/3.0/deed.pt_BR",
        license_verified_at=NOW,
    )
    epe = NewsSource(
        slug="epe",
        publisher_name="Empresa de Pesquisa Energética (EPE)",
        homepage_url="https://www.epe.gov.br",
        fetch_method="listing",
        license_code="CC-BY-4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/deed.pt_BR",
    )
    session.add_all([aneel, epe])
    session.flush()

    def item(source: NewsSource, n: int, hours: int, **kw: Any) -> NewsItem:
        return NewsItem(
            source_id=source.id,
            url=f"https://example.gov.br/n/{n}",
            title=kw.pop("title", f"Notícia {n}"),
            summary=kw.pop("summary", f"Resumo {n}"),
            published_at=NOW - timedelta(hours=hours),
            fetched_at=NOW,
            content_hash=f"h{n}",
            topics=kw.pop("topics", ["outros"]),
            **kw,
        )

    rows = {
        "leilao": item(
            aneel, 1, 1, title="Leilão de transmissão", topics=["leiloes", "transmissao"]
        ),
        "tarifa": item(aneel, 2, 2, title="Reajuste da tarifa", topics=["tarifas"]),
        "epe": item(epe, 3, 3, title="PDE 2036", summary="Plano decenal"),
        "hidden": item(aneel, 4, 0, title="Escondida", status="hidden"),
        "removed": item(aneel, 5, 0, title="Removida", status="removed_on_request"),
    }
    session.add_all(rows.values())
    session.commit()
    return rows


def titles(response: Any) -> list[str]:
    return [i["title"] for i in response.json()["items"]]


def test_list_has_only_published_items_newest_first(
    client: TestClient, news: dict[str, NewsItem]
) -> None:
    response = client.get("/v1/news")

    assert response.status_code == 200
    assert titles(response) == ["Leilão de transmissão", "Reajuste da tarifa", "PDE 2036"]
    assert "Escondida" not in response.text and "Removida" not in response.text


def test_every_item_carries_the_full_reference(
    client: TestClient, news: dict[str, NewsItem]
) -> None:
    first = client.get("/v1/news").json()["items"][0]

    assert first["publisher_name"] == "Agência Nacional de Energia Elétrica (ANEEL)"
    assert first["license_code"] == "CC-BY-ND-3.0"
    assert first["license_url"].startswith("https://creativecommons.org/")
    assert first["url"] == "https://example.gov.br/n/1"
    assert first["published_at"].startswith("2026-10-30T11:00:00")
    assert first["title"] == "Leilão de transmissão" and first["summary"] == "Resumo 1"


def test_hidden_and_removed_items_have_no_detail_page(
    client: TestClient, news: dict[str, NewsItem]
) -> None:
    assert client.get(f"/v1/news/{news['leilao'].id}").status_code == 200
    assert client.get(f"/v1/news/{news['hidden'].id}").status_code == 404
    assert client.get(f"/v1/news/{news['removed'].id}").status_code == 404


def test_filters_by_topic_source_and_text(client: TestClient, news: dict[str, NewsItem]) -> None:
    assert titles(client.get("/v1/news?topic=leiloes")) == ["Leilão de transmissão"]
    assert titles(client.get("/v1/news?topic=transmissao")) == ["Leilão de transmissão"]
    assert titles(client.get("/v1/news?source=epe")) == ["PDE 2036"]
    assert titles(client.get("/v1/news?q=plano")) == ["PDE 2036"]
    assert titles(client.get("/v1/news?q=zzzz")) == []


def test_cursor_pagination_walks_all_items_once(
    client: TestClient, news: dict[str, NewsItem]
) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        url = "/v1/news?limit=1" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url).json()
        seen += [i["title"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == ["Leilão de transmissão", "Reajuste da tarifa", "PDE 2036"]
    assert client.get("/v1/news?cursor=nonsense").status_code == 422


def test_sources_expose_license_and_verification(
    client: TestClient, news: dict[str, NewsItem]
) -> None:
    items = {s["slug"]: s for s in client.get("/v1/news/sources").json()["items"]}

    assert items["epe"]["license_code"] == "CC-BY-4.0"
    assert items["aneel"]["license_verified_at"].startswith("2026-10-30")


def test_admin_requires_authentication(client: TestClient, news: dict[str, NewsItem]) -> None:
    url = f"/v1/admin/news/{news['leilao'].id}"

    assert client.patch(url, json={"status": "hidden"}).status_code == 401
    assert client.get("/v1/admin/news").status_code == 401


def test_hiding_an_item_removes_it_from_the_public_api_at_once(
    client: TestClient, admin_headers: dict[str, str], news: dict[str, NewsItem]
) -> None:
    item_id = news["leilao"].id
    assert "Leilão de transmissão" in titles(client.get("/v1/news"))  # warms the cache

    response = client.patch(
        f"/v1/admin/news/{item_id}", json={"status": "hidden"}, headers=admin_headers
    )

    assert response.status_code == 200 and response.json()["status"] == "hidden"
    assert "Leilão de transmissão" not in titles(client.get("/v1/news"))
    assert client.get(f"/v1/news/{item_id}").status_code == 404


def test_editor_note_is_written_by_a_person_and_kept_apart_from_the_source_text(
    client: TestClient, admin_headers: dict[str, str], news: dict[str, NewsItem]
) -> None:
    item_id = news["tarifa"].id
    response = client.patch(
        f"/v1/admin/news/{item_id}",
        json={
            "editor_note": "Veja o painel de tarifas.",
            "related_links": [{"label": "Painel", "href": "/painel"}],
        },
        headers=admin_headers,
    )
    detail = client.get(f"/v1/news/{item_id}").json()

    assert response.status_code == 200
    assert detail["editor_note"] == "Veja o painel de tarifas."
    assert detail["editor_note_by"] and detail["editor_note_at"]
    assert detail["related_links"] == [{"label": "Painel", "href": "/painel"}]
    assert detail["title"] == "Reajuste da tarifa" and detail["summary"] == "Resumo 2"
    assert client.get("/v1/news").json()["items"][1]["has_editor_note"] is True


def test_patch_rejects_unknown_fields_and_unsafe_links(
    client: TestClient, admin_headers: dict[str, str], news: dict[str, NewsItem]
) -> None:
    url = f"/v1/admin/news/{news['tarifa'].id}"

    assert client.patch(url, json={"title": "Reescrito"}, headers=admin_headers).status_code == 422
    bad = {"related_links": [{"label": "x", "href": "javascript:alert(1)"}]}
    assert client.patch(url, json=bad, headers=admin_headers).status_code == 422


def test_admin_list_shows_hidden_items_too(
    client: TestClient, admin_headers: dict[str, str], news: dict[str, NewsItem]
) -> None:
    body = client.get("/v1/admin/news?status=hidden", headers=admin_headers).json()

    assert [i["title"] for i in body["items"]] == ["Escondida"]


def test_regulatory_acts_carry_the_dou_reference_and_hide_on_request(
    client: TestClient, admin_headers: dict[str, str], session: Session
) -> None:
    act = RegulatoryAct(
        organ="ANEEL",
        act_type="Resolução Normativa",
        act_number="1.000",
        title="Dispõe sobre algo",
        summary="Ementa",
        body="Texto integral",
        dou_date=NOW.date(),
        dou_section=1,
        dou_edition="207",
        dou_page="45",
        dou_url="https://www.in.gov.br/web/dou/-/x",
        topics=["regulacao"],
        content_hash="h",
        fetched_at=NOW,
    )
    session.add(act)
    session.commit()

    listing = client.get("/v1/regulatory-acts?organ=aneel").json()["items"]
    detail = client.get(f"/v1/regulatory-acts/{act.id}").json()

    assert listing[0]["dou_edition"] == "207" and listing[0]["dou_page"] == "45"
    assert "body" not in listing[0] and detail["body"] == "Texto integral"
    patch = client.patch(
        f"/v1/admin/regulatory-acts/{act.id}", json={"status": "hidden"}, headers=admin_headers
    )
    assert patch.status_code == 200
    assert client.get(f"/v1/regulatory-acts/{act.id}").status_code == 404
    assert client.get("/v1/regulatory-acts").json()["items"] == []


def test_revalidate_accepts_the_news_tag(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/v1/admin/revalidate", json={"tags": ["news"]}, headers=admin_headers)

    assert response.status_code == 200 and response.json()["tags"] == ["news"]


def test_machine_clients_cannot_write_editor_notes(
    app_factory: Callable[..., TestClient],
    make_token: Callable[..., str],
    news: dict[str, NewsItem],
) -> None:
    client = app_factory()
    token = make_token(client_id="m2m-client-id", scope="energia-api/admin")
    response = client.patch(
        f"/v1/admin/news/{news['tarifa'].id}",
        json={"editor_note": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code in (401, 403)
