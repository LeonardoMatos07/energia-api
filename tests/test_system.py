from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.cache import PUBLIC_CACHE_CONTROL
from app.models import (
    PlantMonthlyGeneration,
    PowerPlant,
    SubsystemHourlyBalance,
    SubsystemMonthlyGeneration,
)

SEP = date(2026, 9, 1)
AUG = date(2026, 8, 1)


@pytest.fixture
def system_seed(session: Session) -> None:
    start = datetime(2026, 9, 1)  # noqa: DTZ001 - Brasilia wall clock
    for hour in range(48):
        moment = start + timedelta(hours=hour)
        for subsystem in ("NE", "SE"):
            session.add(
                SubsystemHourlyBalance(
                    hour=moment,
                    subsystem=subsystem,
                    hour_utc=moment + timedelta(hours=3),
                    hydro_mw_avg=Decimal("1000.5"),
                    thermal_mw_avg=Decimal("200"),
                    wind_mw_avg=Decimal("300"),
                    solar_mw_avg=Decimal("0"),
                    load_mw_avg=Decimal("1500"),
                    net_interchange_mw_avg=Decimal("-10"),
                )
            )
    for month in (AUG, SEP):
        for source, energy in (("hydro", "5000"), ("wind", "1000")):
            session.add(
                SubsystemMonthlyGeneration(
                    month=month,
                    subsystem="SIN",
                    source=source,
                    energy_mwh=Decimal(energy),
                    avg_mw=Decimal("7"),
                    peak_mw=Decimal("9"),
                    share_pct=Decimal("50.00"),
                )
            )
    session.add(
        PowerPlant(
            id="ITA1",
            name="Itaipu",
            slug="itaipu-ita1",
            source_type="hydro",
            state="PR",
            subsystem="S",
            capacity_mw=Decimal("14000"),
        )
    )
    session.flush()
    for month, factor in ((AUG, "0.7000"), (SEP, "0.7500")):
        session.add(
            PlantMonthlyGeneration(
                month=month,
                plant_id="ITA1",
                energy_mwh=Decimal("7000000"),
                avg_mw=Decimal("10000"),
                peak_mw=Decimal("12000"),
                capacity_factor=Decimal(factor),
            )
        )
    session.commit()


pytestmark = pytest.mark.usefixtures("system_seed")


def test_balance_filters_by_subsystem_and_range(client: TestClient) -> None:
    r = client.get(
        "/v1/system/balance", params={"subsystem": "NE", "from": "2026-09-01", "to": "2026-09-01"}
    )
    assert r.status_code == 200
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    points = r.json()["points"]
    assert len(points) == 24
    assert {p["subsystem"] for p in points} == {"NE"}
    assert points[0]["hydro_mw_avg"] == 1000.5
    assert points[0]["net_interchange_mw_avg"] == -10.0


def test_balance_defaults_to_the_latest_week_of_data(client: TestClient) -> None:
    r = client.get("/v1/system/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["date_to"] == "2026-09-02"
    assert len(body["points"]) == 48 * 2


def test_balance_rejects_more_than_31_days(client: TestClient) -> None:
    r = client.get("/v1/system/balance", params={"from": "2026-08-01", "to": "2026-09-02"})
    assert r.status_code == 422


def test_balance_rejects_inverted_range(client: TestClient) -> None:
    r = client.get("/v1/system/balance", params={"from": "2026-09-02", "to": "2026-09-01"})
    assert r.status_code == 422


def test_generation_defaults_to_the_whole_system(client: TestClient) -> None:
    r = client.get("/v1/system/generation")
    assert r.status_code == 200
    points = r.json()["points"]
    assert [(p["month"], p["source"]) for p in points] == [
        ("2026-08", "hydro"),
        ("2026-08", "wind"),
        ("2026-09", "hydro"),
        ("2026-09", "wind"),
    ]
    assert points[0]["energy_mwh"] == 5000.0


def test_generation_month_filter(client: TestClient) -> None:
    r = client.get("/v1/system/generation", params={"from": "2026-09", "to": "2026-09"})
    assert {p["month"] for p in r.json()["points"]} == {"2026-09"}


def test_generation_for_a_subsystem_without_data_is_empty(client: TestClient) -> None:
    assert client.get("/v1/system/generation", params={"subsystem": "N"}).json() == {"points": []}


def test_plant_generation(client: TestClient) -> None:
    r = client.get("/v1/plants/itaipu-ita1/generation")
    assert r.status_code == 200
    body = r.json()
    assert body["plant"]["source_type"] == "hydro"
    assert [p["month"] for p in body["points"]] == ["2026-08", "2026-09"]
    assert body["points"][1]["capacity_factor"] == 0.75


def test_plant_generation_unknown_slug(client: TestClient) -> None:
    assert client.get("/v1/plants/nope/generation").status_code == 404
