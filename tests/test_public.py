import pytest
from fastapi.testclient import TestClient

from app.cache import PUBLIC_CACHE_CONTROL

pytestmark = pytest.mark.usefixtures("seed")

PLANT_SUMMARY_KEYS = {"id", "kind", "name", "slug", "source", "state", "subsystem", "capacity_mw"}
RANKING_KEYS = PLANT_SUMMARY_KEYS | {
    "rank",
    "curtailed_mwh",
    "curtailment_pct",
    "estimated_loss_brl",
}
AGG_KEYS = {
    "source",
    "month",
    "verified_generation_mwh",
    "curtailed_mwh",
    "curtailment_pct",
    "curtailed_rel_mwh",
    "curtailed_cnf_mwh",
    "curtailed_ene_mwh",
    "curtailed_other_mwh",
    "estimated_loss_brl",
    "affected_plants",
    "updated_at",
}


def test_subsystems_default_total(client: TestClient) -> None:
    r = client.get("/v1/curtailment/subsystems")
    assert r.status_code == 200
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    items = r.json()["items"]
    assert len(items) == 6
    assert {i["source"] for i in items} == {"total"}
    assert set(items[0]) == AGG_KEYS | {"subsystem"}
    assert items[0]["month"] == "2026-07"
    assert items[0]["curtailed_mwh"] == 90.0
    assert isinstance(items[0]["estimated_loss_brl"], float)


def test_subsystems_filters(client: TestClient) -> None:
    r = client.get("/v1/curtailment/subsystems", params={"source": "wind", "start": "2026-08"})
    items = r.json()["items"]
    assert [i["month"] for i in items] == ["2026-08", "2026-09"]
    assert {i["source"] for i in items} == {"wind"}

    r = client.get("/v1/curtailment/subsystems", params={"start": "2026-08", "end": "2026-08"})
    assert {i["month"] for i in r.json()["items"]} == {"2026-08"}


def test_subsystems_subsystem_filter(client: TestClient) -> None:
    r = client.get("/v1/curtailment/subsystems", params={"subsystem": "NE"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["month"] for i in items] == ["2026-07", "2026-08", "2026-09"]
    assert {(i["subsystem"], i["source"]) for i in items} == {("NE", "total")}

    r = client.get(
        "/v1/curtailment/subsystems",
        params={"subsystem": "SIN", "source": "wind", "end": "2026-07"},
    )
    assert [(i["subsystem"], i["month"]) for i in r.json()["items"]] == [("SIN", "2026-07")]

    r = client.get("/v1/curtailment/subsystems", params={"subsystem": "S"})
    assert r.json() == {"items": []}


@pytest.mark.parametrize(
    "params",
    [
        {"source": "coal"},
        {"start": "2026-13"},
        {"end": "2026-9"},
        {"subsystem": "XX"},
        {"subsystem": "ne"},
    ],
)
def test_subsystems_invalid_params(client: TestClient, params: dict[str, str]) -> None:
    r = client.get("/v1/curtailment/subsystems", params=params)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


def test_state_default(client: TestClient) -> None:
    r = client.get("/v1/curtailment/states/ba")
    assert r.status_code == 200
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    body = r.json()
    assert set(body) == {"series", "ranking", "ranking_month"}
    assert [s["month"] for s in body["series"]] == ["2026-08", "2026-09"]
    assert set(body["series"][0]) == AGG_KEYS | {"state"}
    assert body["ranking_month"] == "2026-09"
    ranking = body["ranking"]
    assert [p["id"] for p in ranking] == ["BAU1", "BAS1", "BAU2"]
    assert [p["rank"] for p in ranking] == [1, 2, 3]
    assert set(ranking[0]) == RANKING_KEYS


def test_state_source_and_month(client: TestClient) -> None:
    body = client.get(
        "/v1/curtailment/states/BA", params={"source": "wind", "month": "2026-08"}
    ).json()
    assert {s["source"] for s in body["series"]} == {"wind"}
    assert body["ranking_month"] == "2026-08"
    assert [(p["id"], p["rank"]) for p in body["ranking"]] == [("BAU1", 1)]


def test_state_unknown(client: TestClient) -> None:
    assert client.get("/v1/curtailment/states/XX").status_code == 404
    assert client.get("/v1/curtailment/states/BAH").status_code == 422


def test_state_without_data(client: TestClient) -> None:
    body = client.get("/v1/curtailment/states/SP").json()
    assert body == {"series": [], "ranking": [], "ranking_month": None}


def test_plants_list_and_filters(client: TestClient) -> None:
    r = client.get("/v1/plants")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    body = r.json()
    assert body["total"] == 5 and body["page"] == 1 and body["page_size"] == 20
    assert set(body["items"][0]) == PLANT_SUMMARY_KEYS

    body = client.get("/v1/plants", params={"page_size": 2, "page": 2}).json()
    assert body["total"] == 5 and len(body["items"]) == 2 and body["page"] == 2

    body = client.get("/v1/plants", params={"search": "ALPHA"}).json()
    assert {p["id"] for p in body["items"]} == {"CJU_BA1", "BAU1", "BAU2"}

    body = client.get("/v1/plants", params={"state": "ba", "source": "solar"}).json()
    assert [p["id"] for p in body["items"]] == ["BAS1"]

    body = client.get("/v1/plants", params={"kind": "cluster"}).json()
    assert [p["id"] for p in body["items"]] == ["CJU_BA1"]

    body = client.get("/v1/plants", params={"search": "100%"}).json()
    assert body["total"] == 0

    assert client.get("/v1/plants", params={"page_size": 101}).status_code == 422


def test_plant_detail_member(client: TestClient) -> None:
    r = client.get("/v1/plants/alpha-i-bau1")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"plant", "cluster", "cluster_plants", "series"}
    assert set(body["plant"]) == PLANT_SUMMARY_KEYS | {
        "cluster_id",
        "ceg",
        "connection_point",
        "operator",
        "updated_at",
    }
    assert body["cluster"]["id"] == "CJU_BA1"
    assert {p["id"] for p in body["cluster_plants"]} == {"BAU1", "BAU2"}
    assert [s["month"] for s in body["series"]] == ["2026-08", "2026-09"]
    assert body["series"][1]["state_rank"] == 1
    assert body["series"][1]["plant_id"] == "BAU1"


def test_plant_detail_cluster_and_standalone(client: TestClient) -> None:
    body = client.get("/v1/plants/alpha-cluster-cjuba1").json()
    assert body["cluster"] is None
    assert {p["id"] for p in body["cluster_plants"]} == {"BAU1", "BAU2"}

    body = client.get("/v1/plants/bahia-sun-bas1").json()
    assert body["cluster"] is None and body["cluster_plants"] == []

    r = client.get("/v1/plants/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {"detail": "Plant not found."}


def test_ranking(client: TestClient) -> None:
    r = client.get("/v1/ranking")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    body = r.json()
    assert body["month"] == "2026-09"
    assert [p["id"] for p in body["items"]] == ["RNU1", "BAU1", "BAS1", "BAU2"]
    assert [p["rank"] for p in body["items"]] == [1, 2, 3, 4]
    assert set(body["items"][0]) == RANKING_KEYS

    body = client.get("/v1/ranking", params={"source": "wind"}).json()
    assert [(p["id"], p["rank"]) for p in body["items"]] == [
        ("RNU1", 1),
        ("BAU1", 2),
        ("BAU2", 3),
    ]

    body = client.get("/v1/ranking", params={"state": "BA", "limit": 1}).json()
    assert [p["id"] for p in body["items"]] == ["BAU1"]

    body = client.get("/v1/ranking", params={"month": "2026-08"}).json()
    assert body["month"] == "2026-08" and [p["id"] for p in body["items"]] == ["BAU1"]

    assert client.get("/v1/ranking", params={"limit": 101}).status_code == 422


def test_months(client: TestClient) -> None:
    r = client.get("/v1/months")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert r.json() == {"months": ["2026-09", "2026-08", "2026-07"]}


def test_reports(client: TestClient) -> None:
    r = client.get("/v1/reports")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    items = r.json()["items"]
    assert [i["slug"] for i in items] == ["report-august-2026"]
    assert set(items[0]) == {"month", "title", "slug", "summary", "published_at"}
    assert items[0]["month"] == "2026-08"

    r = client.get("/v1/reports/report-august-2026")
    assert r.status_code == 200
    assert r.json()["content"] == "# August"

    r = client.get("/v1/reports/report-september-2026")
    assert r.status_code == 404
    assert r.json() == {"detail": "Report not found."}


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_summary_matches_the_monthly_rows(client: TestClient) -> None:
    rows = client.get("/v1/curtailment/subsystems", params={"subsystem": "SIN"}).json()["items"]
    r = client.get("/v1/curtailment/summary", params={"scope": "SIN"})
    assert r.status_code == 200
    body = r.json()
    assert body["months"] == len(rows)
    assert body["curtailed_mwh"] == pytest.approx(sum(row["curtailed_mwh"] for row in rows))
    assert body["verified_generation_mwh"] == pytest.approx(
        sum(row["verified_generation_mwh"] for row in rows)
    )
    potential = body["curtailed_mwh"] + body["verified_generation_mwh"]
    assert body["curtailment_pct"] == pytest.approx(
        body["curtailed_mwh"] / potential * 100, abs=0.01
    )


def test_summary_period_and_source(client: TestClient) -> None:
    r = client.get(
        "/v1/curtailment/summary",
        params={"scope": "SIN", "source": "wind", "start": "2026-08", "end": "2026-09"},
    )
    body = r.json()
    rows = client.get(
        "/v1/curtailment/subsystems",
        params={"subsystem": "SIN", "source": "wind", "start": "2026-08", "end": "2026-09"},
    ).json()["items"]
    assert body["months"] == 2
    assert body["start"] == "2026-08"
    assert body["curtailed_mwh"] == pytest.approx(sum(row["curtailed_mwh"] for row in rows))


def test_summary_state_scope_is_case_insensitive(client: TestClient) -> None:
    upper = client.get("/v1/curtailment/summary", params={"scope": "BA"})
    lower = client.get("/v1/curtailment/summary", params={"scope": "ba"})
    assert upper.status_code == lower.status_code == 200
    assert upper.json() == lower.json()
    assert upper.json()["affected_plants"] >= 0


def test_summary_counts_each_plant_once(client: TestClient) -> None:
    whole = client.get("/v1/curtailment/summary").json()
    one_month = client.get(
        "/v1/curtailment/summary", params={"start": "2026-09", "end": "2026-09"}
    ).json()
    assert whole["affected_plants"] >= one_month["affected_plants"]
    assert whole["affected_plants"] <= sum(
        client.get("/v1/curtailment/summary", params={"start": month, "end": month}).json()[
            "affected_plants"
        ]
        for month in ("2026-07", "2026-08", "2026-09")
    )


def test_summary_rejects_unknown_scope_and_reversed_period(client: TestClient) -> None:
    assert client.get("/v1/curtailment/summary", params={"scope": "XX"}).status_code == 404
    reversed_period = {"start": "2026-09", "end": "2026-07"}
    assert client.get("/v1/curtailment/summary", params=reversed_period).status_code == 422
