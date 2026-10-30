from fastapi.testclient import TestClient

from app.cache import PUBLIC_CACHE_CONTROL


def test_states_default_is_the_latest_month_of_the_total(client: TestClient, seed: None) -> None:
    r = client.get("/v1/geo/states/curtailment")
    assert r.status_code == 200
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    body = r.json()
    assert set(body) == {"meta", "items"}
    meta = body["meta"]
    assert meta["month"] == "2026-09"
    assert meta["months"] == ["2026-08", "2026-09"]
    assert meta["data_source"] == "total"
    assert meta["omitted"] == 26
    assert "BA" not in meta["omitted_states"]
    assert "SP" in meta["omitted_states"]
    assert meta["warnings"]
    assert [i["state"] for i in body["items"]] == ["BA"]
    assert set(body["items"][0]) == {
        "state",
        "curtailment_pct",
        "curtailed_mwh",
        "verified_generation_mwh",
        "estimated_loss_brl",
        "affected_plants",
    }
    assert "geometry" not in body["items"][0]


def test_states_source_and_month(client: TestClient, seed: None) -> None:
    body = client.get(
        "/v1/geo/states/curtailment", params={"source": "wind", "month": "2026-08"}
    ).json()
    assert body["meta"]["month"] == "2026-08"
    assert body["meta"]["data_source"] == "wind"
    assert [i["state"] for i in body["items"]] == ["BA"]


def test_states_month_without_data_is_404_and_bad_input_is_422(
    client: TestClient, seed: None
) -> None:
    assert client.get("/v1/geo/states/curtailment", params={"month": "2020-01"}).status_code == 404
    assert client.get("/v1/geo/states/curtailment", params={"month": "2026-13"}).status_code == 422
    assert client.get("/v1/geo/states/curtailment", params={"source": "coal"}).status_code == 422


def test_states_without_any_data(client: TestClient) -> None:
    body = client.get("/v1/geo/states/curtailment").json()
    assert body["items"] == []
    assert body["meta"]["month"] is None
    assert body["meta"]["omitted"] == 27
