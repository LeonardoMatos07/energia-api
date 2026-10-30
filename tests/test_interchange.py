from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import InterchangeHourly, InterchangeMonthly, ThermalDispatchMonthly

AUG = date(2026, 8, 1)
SEP = date(2026, 9, 1)


@pytest.fixture
def interchange_seed(session: Session) -> None:
    start = datetime(2026, 9, 1)  # noqa: DTZ001 - Brasilia wall clock
    for hour in range(48):
        moment = start + timedelta(hours=hour)
        session.add(
            InterchangeHourly(
                hour=moment,
                origin="NE",
                destination="SE",
                scope="national",
                hour_utc=moment + timedelta(hours=3),
                flow_mw_avg=Decimal("1500.5"),
                scheduled_mw_avg=Decimal("1400"),
            )
        )
        session.add(
            InterchangeHourly(
                hour=moment,
                origin="BR",
                destination="ARGENTINA",
                scope="international",
                hour_utc=moment + timedelta(hours=3),
                flow_mw_avg=Decimal("-20"),
                scheduled_mw_avg=None,
            )
        )
    for month in (AUG, SEP):
        for origin, destination, scope in (
            ("NE", "SE", "national"),
            ("BR", "ARGENTINA", "international"),
        ):
            session.add(
                InterchangeMonthly(
                    month=month,
                    origin=origin,
                    destination=destination,
                    scope=scope,
                    energy_mwh=Decimal("1000000"),
                    scheduled_energy_mwh=Decimal("900000"),
                    avg_mw=Decimal("1400"),
                    peak_mw=Decimal("3000"),
                    min_mw=Decimal("-50"),
                )
            )
    for plant, name, subsystem, merit in (
        ("201", "Aparecida", "N", "100"),
        ("202", "Mauá", "N", "50"),
    ):
        for reason, value in (("total", "300"), ("merit_order", merit)):
            session.add(
                ThermalDispatchMonthly(
                    month=SEP,
                    plant_code=plant,
                    reason=reason,
                    plant_name=name,
                    ceg=f"UTE.{plant}",
                    fuel_type="GAS",
                    subsystem=subsystem,
                    verified_mwh=Decimal(value),
                    programmed_mwh=Decimal(value),
                )
            )
    session.commit()


pytestmark = pytest.mark.usefixtures("interchange_seed")


def test_monthly_interchange_filters(client: TestClient) -> None:
    body = client.get("/v1/system/interchange", params={"origin": "ne", "destination": "SE"}).json()

    assert [(p["month"], p["origin"], p["destination"]) for p in body["points"]] == [
        ("2026-08", "NE", "SE"),
        ("2026-09", "NE", "SE"),
    ]
    assert body["points"][0]["energy_mwh"] == 1_000_000.0
    assert body["points"][0]["min_mw"] == -50.0


def test_monthly_interchange_scope_and_range(client: TestClient) -> None:
    body = client.get(
        "/v1/system/interchange", params={"scope": "international", "from": "2026-09"}
    ).json()

    assert [(p["month"], p["destination"]) for p in body["points"]] == [("2026-09", "ARGENTINA")]


def test_hourly_interchange_keeps_the_sign(client: TestClient) -> None:
    r = client.get(
        "/v1/system/interchange/hourly",
        params={"origin": "BR", "from": "2026-09-01", "to": "2026-09-01"},
    )

    points = r.json()["points"]
    assert len(points) == 24
    assert points[0]["flow_mw_avg"] == -20.0
    assert points[0]["scheduled_mw_avg"] is None


def test_hourly_interchange_limits_the_range(client: TestClient) -> None:
    r = client.get(
        "/v1/system/interchange/hourly", params={"from": "2026-08-01", "to": "2026-09-30"}
    )
    assert r.status_code == 422


def test_thermal_dispatch_is_summed_per_subsystem_and_reason(client: TestClient) -> None:
    points = client.get("/v1/system/thermal-dispatch").json()["points"]

    by_reason = {p["reason"]: p for p in points}
    assert by_reason["merit_order"]["verified_mwh"] == 150.0
    assert by_reason["merit_order"]["plants"] == 2
    assert by_reason["total"]["verified_mwh"] == 600.0
    assert by_reason["merit_order"]["plant_code"] is None


def test_thermal_dispatch_per_plant_and_reason(client: TestClient) -> None:
    points = client.get(
        "/v1/system/thermal-dispatch", params={"plant": "202", "reason": "merit_order"}
    ).json()["points"]

    assert len(points) == 1
    assert points[0]["plant_name"] == "Mauá"
    assert points[0]["verified_mwh"] == 50.0


def test_thermal_dispatch_filters_by_subsystem(client: TestClient) -> None:
    assert client.get("/v1/system/thermal-dispatch", params={"subsystem": "S"}).json() == {
        "points": []
    }
