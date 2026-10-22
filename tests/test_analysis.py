from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import HourlyCurtailment, Plant

pytestmark = pytest.mark.usefixtures("seed")

AUG = date(2026, 8, 1)
SEP = date(2026, 9, 1)


@pytest.fixture
def grouped(seed: None, session: Session) -> None:
    assignments = {
        "BAU1": ("ALFA", "PC1"),
        "BAU2": ("ALFA", "PC1"),
        "BAS1": ("BETA", "PC1"),
        "RNU1": ("GAMA", "PC2"),
    }
    for plant_id, (operator, point) in assignments.items():
        session.execute(
            update(Plant)
            .where(Plant.id == plant_id)
            .values(operator=operator, connection_point=point)
        )
    session.commit()


def add_hourly(session: Session) -> None:
    rows = [
        (AUG, "weekday", 10, "200.000", "600.000", 20),
        (SEP, "weekday", 10, "100.000", "300.000", 20),
        (AUG, "weekend", 10, "90.000", "10.000", 8),
        (SEP, "weekday", 11, "40.000", "160.000", 20),
    ]
    for month, day_type, hour, curtailed, generation, days in rows:
        session.add(
            HourlyCurtailment(
                subsystem="SIN",
                source="total",
                month=month,
                day_type=day_type,
                hour=hour,
                curtailed_mwh=Decimal(curtailed),
                generation_mwh=Decimal(generation),
                days=days,
            )
        )
    session.commit()


def test_summary_includes_the_reasons(client: TestClient) -> None:
    body = client.get("/v1/curtailment/summary", params={"scope": "SIN"}).json()
    assert body["curtailed_rel_mwh"] == 30.0
    assert body["curtailed_cnf_mwh"] == 60.0
    assert body["curtailed_ene_mwh"] == 90.0
    assert body["curtailed_other_mwh"] == 0.0


@pytest.mark.usefixtures("grouped")
def test_operator_ranking_orders_by_curtailed_energy(client: TestClient) -> None:
    body = client.get("/v1/rankings/operators").json()
    assert body["group"] == "operator"
    assert [(item["rank"], item["name"]) for item in body["items"]] == [
        (1, "ALFA"),
        (2, "GAMA"),
        (3, "BETA"),
    ]
    alfa = body["items"][0]
    assert alfa["curtailed_mwh"] == 1000.0
    assert alfa["plants"] == 2
    assert alfa["affected_plants"] == 2
    assert alfa["compensable_mwh"] == 9.0
    assert alfa["state"] is None
    potential = alfa["curtailed_mwh"] + alfa["verified_generation_mwh"]
    assert alfa["curtailment_pct"] == pytest.approx(
        alfa["curtailed_mwh"] / potential * 100, abs=0.01
    )


@pytest.mark.usefixtures("grouped")
def test_operator_ranking_filters(client: TestClient) -> None:
    solar = client.get("/v1/rankings/operators", params={"source": "solar"}).json()
    assert [item["name"] for item in solar["items"]] == ["BETA"]

    september = client.get(
        "/v1/rankings/operators", params={"start": "2026-09", "end": "2026-09"}
    ).json()
    assert september["items"][0]["name"] == "ALFA"
    assert september["items"][0]["curtailed_mwh"] == 900.0

    assert len(client.get("/v1/rankings/operators", params={"limit": 1}).json()["items"]) == 1


@pytest.mark.usefixtures("grouped")
def test_connection_point_ranking_reports_the_state(client: TestClient) -> None:
    items = client.get("/v1/rankings/connection-points").json()["items"]
    assert [(item["name"], item["curtailed_mwh"], item["state"]) for item in items] == [
        ("PC1", 1450.0, "BA"),
        ("PC2", 800.0, "RN"),
    ]


def test_rankings_skip_plants_without_the_group(client: TestClient) -> None:
    assert client.get("/v1/rankings/operators").json()["items"] == []


def test_rankings_reject_a_reversed_period(client: TestClient) -> None:
    response = client.get("/v1/rankings/operators", params={"start": "2026-09", "end": "2026-07"})
    assert response.status_code == 422


def test_hourly_profile_averages_per_day(client: TestClient, session: Session) -> None:
    add_hourly(session)
    body = client.get("/v1/curtailment/hourly").json()
    assert body["scope"] == "SIN"
    assert body["months"] == 2
    points = {(p["day_type"], p["hour"]): p for p in body["points"]}
    assert set(points) == {("weekday", 10), ("weekday", 11), ("weekend", 10)}
    weekday = points[("weekday", 10)]
    assert weekday["avg_curtailed_mwh"] == pytest.approx(7.5)
    assert weekday["avg_generation_mwh"] == pytest.approx(22.5)
    assert weekday["curtailment_pct"] == pytest.approx(25.0)


def test_hourly_profile_respects_the_period(client: TestClient, session: Session) -> None:
    add_hourly(session)
    body = client.get(
        "/v1/curtailment/hourly", params={"start": "2026-09", "end": "2026-09"}
    ).json()
    assert body["months"] == 1
    weekday = next(p for p in body["points"] if (p["day_type"], p["hour"]) == ("weekday", 10))
    assert weekday["avg_curtailed_mwh"] == pytest.approx(5.0)


def test_hourly_profile_without_data_is_empty(client: TestClient) -> None:
    body = client.get("/v1/curtailment/hourly", params={"scope": "NE", "source": "solar"}).json()
    assert body["points"] == []
    assert body["months"] == 0
    assert client.get("/v1/curtailment/hourly", params={"scope": "BA"}).status_code == 422
