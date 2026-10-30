from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    PlantMonthlySpilledEnergy,
    Reservoir,
    ReservoirDaily,
    SubsystemStorageDaily,
)

SEP = date(2026, 9, 1)


@pytest.fixture
def hydro_seed(session: Session) -> None:
    for day in range(1, 11):
        for subsystem, pct in (("SE", "40.5"), ("NE", "60")):
            session.add(
                SubsystemStorageDaily(
                    day=date(2026, 9, day),
                    subsystem=subsystem,
                    storage_max_mw_month=Decimal("200000"),
                    storage_mw_month=Decimal("81000"),
                    storage_pct=Decimal(pct),
                    ena_gross_mw_avg=Decimal("30000"),
                    ena_gross_pct_mlt=Decimal("75.5"),
                    ena_storable_mw_avg=Decimal("29000"),
                    ena_storable_pct_mlt=Decimal("74"),
                )
            )
    session.add_all(
        [
            Reservoir(
                id="JEUITP",
                name="ITAPEBI",
                slug="itapebi-jeuitp",
                reservoir_type="FIO DAGUA",
                subsystem="NE",
                basin="JEQUITINHONHA",
                river="JEQUITINHONHA",
                useful_volume_hm3=Decimal("35"),
            ),
            Reservoir(
                id="GRAVER",
                name="A. VERMELHA",
                slug="a-vermelha-graver",
                subsystem="SE",
                basin="GRANDE",
            ),
        ]
    )
    session.flush()
    for day in range(1, 4):
        session.add(
            ReservoirDaily(
                day=date(2026, 9, day),
                reservoir_id="GRAVER",
                storage_pct=Decimal("27.5"),
                ena_storable_pct_mlt=Decimal("80"),
                inflow_m3s=Decimal("700"),
                spilled_m3s=Decimal("0"),
                upstream_level_m=Decimal("385.2"),
            )
        )
    session.add_all(
        [
            PlantMonthlySpilledEnergy(
                month=SEP,
                plant_code="99",
                plant_name="14 DE JULHO",
                subsystem="S",
                spilled_energy_mwh=Decimal("500"),
                spilled_turbinable_energy_mwh=Decimal("120"),
            ),
            PlantMonthlySpilledEnergy(
                month=SEP,
                plant_code="154",
                plant_name="ITAPEBI",
                subsystem="NE",
                spilled_energy_mwh=Decimal("900"),
                spilled_turbinable_energy_mwh=Decimal("300"),
            ),
        ]
    )
    session.commit()


pytestmark = pytest.mark.usefixtures("hydro_seed")


def test_subsystems_default_to_the_latest_90_days(client: TestClient) -> None:
    body = client.get("/v1/hydro/subsystems").json()

    assert body["date_to"] == "2026-09-10"
    assert len(body["points"]) == 20
    assert body["points"][0]["storage_pct"] == 60.0  # NE before SE on the same day


def test_subsystems_filter(client: TestClient) -> None:
    r = client.get(
        "/v1/hydro/subsystems",
        params={"subsystem": "SE", "from": "2026-09-03", "to": "2026-09-04"},
    )

    assert [p["day"] for p in r.json()["points"]] == ["2026-09-03", "2026-09-04"]


def test_subsystems_reject_a_range_over_a_year(client: TestClient) -> None:
    r = client.get("/v1/hydro/subsystems", params={"from": "2025-01-01", "to": "2026-09-10"})
    assert r.status_code == 422


def test_reservoir_list_has_the_latest_level(client: TestClient) -> None:
    body = client.get("/v1/hydro/reservoirs").json()

    assert body["latest_day"] == "2026-09-03"
    by_name = {item["name"]: item for item in body["items"]}
    assert by_name["A. VERMELHA"]["storage_pct"] == 27.5
    assert by_name["ITAPEBI"]["storage_pct"] is None  # no data on the latest day


def test_reservoir_list_filters_by_subsystem_and_basin(client: TestClient) -> None:
    names = [
        i["name"]
        for i in client.get("/v1/hydro/reservoirs", params={"subsystem": "NE"}).json()["items"]
    ]
    assert names == ["ITAPEBI"]
    grande = client.get("/v1/hydro/reservoirs", params={"basin": "grande"}).json()["items"]
    assert [i["name"] for i in grande] == ["A. VERMELHA"]


def test_reservoir_detail(client: TestClient) -> None:
    body = client.get("/v1/hydro/reservoirs/a-vermelha-graver").json()

    assert body["reservoir"]["subsystem"] == "SE"
    assert [p["day"] for p in body["points"]] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert body["points"][0]["inflow_m3s"] == 700.0


def test_reservoir_detail_unknown(client: TestClient) -> None:
    assert client.get("/v1/hydro/reservoirs/nope").status_code == 404


def test_spilled_energy_is_sorted_by_turbinable_energy(client: TestClient) -> None:
    points = client.get("/v1/hydro/spilled-energy").json()["points"]

    assert [p["plant_name"] for p in points] == ["ITAPEBI", "14 DE JULHO"]
    only_south = client.get("/v1/hydro/spilled-energy", params={"subsystem": "S"}).json()["points"]
    assert [p["plant_code"] for p in only_south] == ["99"]
