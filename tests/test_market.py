# ruff: noqa: DTZ001
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    ConsumerUnitSet,
    DataSource,
    Distributor,
    PldDaily,
    PldHourly,
    PldMonthly,
    PldWeekly,
    ReliabilityMonthly,
)


def _source(slug: str, license_code: str) -> DataSource:
    return DataSource(
        slug=slug,
        publisher_name="Publisher",
        dataset_name="Dataset",
        dataset_url="https://example.org/dataset",
        license_code=license_code,
        license_url="https://example.org/license",
        license_verified_at=date(2026, 10, 30),
        attribution_text=f"Fonte: {slug}",
        update_frequency="Mensal",
    )


@pytest.fixture
def market_seed(session: Session) -> None:
    session.add_all([_source("ccee-pld", "CC-BY-4.0"), _source("aneel-reliability", "ODbL-1.0")])
    for hour in (0, 1):
        for subsystem, price in (("SE", "100.50"), ("NE", "90")):
            session.add(
                PldHourly(
                    hour_utc=datetime(2026, 9, 1, hour + 3),
                    hour=datetime(2026, 9, 1, hour),
                    subsystem=subsystem,
                    variant="official",
                    method="official",
                    pld_brl_per_mwh=Decimal(price),
                )
            )
    session.add(
        PldHourly(
            hour_utc=datetime(2024, 8, 1, 3),
            hour=datetime(2024, 8, 1, 0),
            subsystem="SE",
            variant="shadow",
            method="NEWAVE_HIBRIDO",
            pld_brl_per_mwh=Decimal("197.28"),
        )
    )
    stats = {
        "pld_avg_brl_per_mwh": Decimal("100"),
        "pld_min_brl_per_mwh": Decimal("57.31"),
        "pld_max_brl_per_mwh": Decimal("250"),
        "pld_std_brl_per_mwh": Decimal("40"),
        "hours_at_floor": 3,
        "hours_at_cap": 0,
        "hours": 24,
    }
    session.add_all(
        [
            PldDaily(day=date(2026, 9, 1), subsystem="SE", **stats),
            PldMonthly(month=date(2026, 9, 1), subsystem="SE", **stats),
            PldWeekly(
                week_start=date(2026, 9, 5), subsystem="SE", pld_avg_brl_per_mwh=Decimal("111.1")
            ),
        ]
    )
    session.add_all(
        [
            Distributor(cnpj="1", acronym="EDP ES"),
            Distributor(cnpj="2", acronym="CPFL"),
        ]
    )
    session.flush()
    session.add_all(
        [
            ConsumerUnitSet(distributor_cnpj="1", set_id="a", set_name="Set A"),
            ConsumerUnitSet(distributor_cnpj="1", set_id="b", set_name="Set B"),
            ConsumerUnitSet(distributor_cnpj="2", set_id="c", set_name="Set C"),
        ]
    )
    session.flush()
    for month in (date(2026, 1, 1), date(2026, 2, 1)):
        session.add_all(
            [
                ReliabilityMonthly(
                    month=month,
                    distributor_cnpj="1",
                    set_id="a",
                    consumers=100,
                    dec_hours=Decimal("1.00"),
                    fec_count=Decimal("0.50"),
                    dec_limit_hours=Decimal("10"),
                    fec_limit_count=Decimal("8"),
                ),
                ReliabilityMonthly(
                    month=month,
                    distributor_cnpj="1",
                    set_id="b",
                    consumers=300,
                    dec_hours=Decimal("2.00"),
                    fec_count=Decimal("1.50"),
                    dec_limit_hours=Decimal("10"),
                    fec_limit_count=Decimal("8"),
                ),
                ReliabilityMonthly(
                    month=month,
                    distributor_cnpj="2",
                    set_id="c",
                    consumers=50,
                    dec_hours=Decimal("0.20"),
                    fec_count=Decimal("0.10"),
                ),
            ]
        )
    session.commit()


def test_daily_pld_carries_the_source_and_statistics(client: TestClient, market_seed: None) -> None:
    body = client.get("/v1/market/pld", params={"granularity": "daily"}).json()

    assert body["source"]["license_code"] == "CC-BY-4.0"
    assert body["source"]["attribution_text"] == "Fonte: ccee-pld"
    point = body["points"][0]
    assert point["period"] == "2026-09-01" and point["subsystem"] == "SE"
    assert point["pld_min_brl_per_mwh"] == 57.31 and point["hours_at_floor"] == 3


def test_hourly_pld_filters_by_subsystem_and_limits_the_window(
    client: TestClient, market_seed: None
) -> None:
    body = client.get("/v1/market/pld", params={"granularity": "hourly", "subsystem": "NE"}).json()
    assert [p["pld_avg_brl_per_mwh"] for p in body["points"]] == [90.0, 90.0]
    assert body["points"][0]["period"].startswith("2026-09-01T00:00")

    too_long = client.get(
        "/v1/market/pld",
        params={"granularity": "hourly", "from": "2026-01-01", "to": "2026-09-01"},
    )
    assert too_long.status_code == 422


def test_weekly_and_monthly_pld(client: TestClient, market_seed: None) -> None:
    weekly = client.get("/v1/market/pld", params={"granularity": "weekly"}).json()
    monthly = client.get("/v1/market/pld", params={"granularity": "monthly"}).json()

    assert weekly["points"][0]["pld_avg_brl_per_mwh"] == 111.1
    assert monthly["points"][0]["period"] == "2026-09-01"


def test_shadow_pld_is_hourly_only_and_labelled(client: TestClient, market_seed: None) -> None:
    refused = client.get("/v1/market/pld", params={"variant": "shadow", "granularity": "daily"})
    assert refused.status_code == 422

    body = client.get(
        "/v1/market/pld",
        params={
            "variant": "shadow",
            "granularity": "hourly",
            "from": "2024-08-01",
            "to": "2024-08-01",
        },
    ).json()
    assert body["variant"] == "shadow" and body["points"][0]["pld_avg_brl_per_mwh"] == 197.28


def test_pld_without_a_registered_source_is_unavailable(client: TestClient) -> None:
    assert client.get("/v1/market/pld").status_code == 503


def test_reliability_ranking_weights_sets_by_consumers(
    client: TestClient, market_seed: None
) -> None:
    body = client.get("/v1/reliability").json()

    assert body["year"] == 2026 and body["years"] == [2026] and body["months_with_data"] == 2
    assert body["source"]["license_code"] == "ODbL-1.0"
    worst = body["items"][0]
    # monthly DEC = (1*100 + 2*300) / 400 = 1.75, over two months = 3.5
    assert worst["acronym"] == "EDP ES" and worst["dec_hours"] == 3.5
    assert worst["fec_count"] == 2.5  # (0.5*100 + 1.5*300) / 400 = 1.25 per month
    assert worst["dec_limit_hours"] == 10.0 and worst["consumers"] == 400
    assert body["items"][1]["dec_limit_hours"] is None


def test_reliability_detail_is_monthly(client: TestClient, market_seed: None) -> None:
    body = client.get("/v1/reliability/1", params={"year": 2026}).json()

    assert [p["month"] for p in body["points"]] == ["2026-01-01", "2026-02-01"]
    assert body["points"][0]["dec_hours"] == 1.75
    assert body["distributor"]["acronym"] == "EDP ES"
    assert client.get("/v1/reliability/999").status_code == 404


def test_reliability_without_data_is_404(client: TestClient) -> None:
    assert client.get("/v1/reliability").status_code == 404
