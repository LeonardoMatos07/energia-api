from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DataSource, MarketAgentsMonthly, MarketConsumptionMonthly, TariffFlagMonthly


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
def seed(session: Session) -> None:
    session.add_all(
        [_source("aneel-tariff-flags", "ODbL-1.0"), _source("ccee-market", "CC-BY-4.0")]
    )
    session.add_all(
        [
            TariffFlagMonthly(
                month=date(2026, 9, 1), flag="Amarela", additional_brl_per_mwh=Decimal("18.85")
            ),
            TariffFlagMonthly(
                month=date(2026, 10, 1), flag="Verde", additional_brl_per_mwh=Decimal("0")
            ),
            MarketConsumptionMonthly(
                month=date(2026, 9, 1),
                regulated_mw_avg=Decimal("45000"),
                free_mw_avg=Decimal("15000"),
            ),
            MarketAgentsMonthly(
                month=date(2026, 9, 1), agent_class="Consumidor Especial", agents=9700
            ),
            MarketAgentsMonthly(month=date(2026, 9, 1), agent_class="Comercializador", agents=520),
        ]
    )
    session.commit()


def test_tariff_flags_are_listed_with_the_source_and_filtered_by_month(
    client: TestClient, seed: None
) -> None:
    body = client.get("/v1/tariffs/flags").json()
    assert [i["flag"] for i in body["items"]] == ["Amarela", "Verde"]
    assert body["items"][0]["additional_brl_per_mwh"] == 18.85
    assert body["source"]["license_code"] == "ODbL-1.0"

    later = client.get("/v1/tariffs/flags", params={"from": "2026-10-01"}).json()
    assert [i["flag"] for i in later["items"]] == ["Verde"]


def test_market_consumption_has_the_free_market_share(client: TestClient, seed: None) -> None:
    body = client.get("/v1/market/consumption").json()

    assert body["consumption"][0]["free_share_pct"] == 25.0
    assert [a["agent_class"] for a in body["agents"]] == ["Comercializador", "Consumidor Especial"]
    assert body["source"]["attribution_text"] == "Fonte: ccee-market"


def test_without_a_registered_source_the_endpoints_are_unavailable(client: TestClient) -> None:
    assert client.get("/v1/tariffs/flags").status_code == 503
    assert client.get("/v1/market/consumption").status_code == 503
