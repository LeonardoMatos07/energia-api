from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Substation, TransformerCapacity, TransmissionLine

JAN = date(2026, 1, 10)
JUN = date(2026, 6, 10)


def _line(code: str, kv: str, from_state: str, to_state: str, **kw: object) -> TransmissionLine:
    values: dict[str, object] = {
        "equipment_code": code,
        "valid_from": JAN,
        "row_hash": f"hash-{code}",
        "name": f"LT {kv} kV {code}",
        "voltage_kv": Decimal(kv),
        "from_state": from_state,
        "to_state": to_state,
        "from_subsystem": "NE",
        "to_subsystem": "SE",
        "length_km": Decimal("120.5"),
        "capacity_long_no_limit_a": Decimal("780"),
    }
    values.update(kw)
    return TransmissionLine(**values)


@pytest.fixture
def grid_seed(session: Session) -> None:
    session.add_all(
        [
            _line("A", "500", "BA", "MG"),
            _line("B", "230", "BA", "BA"),
            _line("C", "230", "SP", "SP", from_subsystem="SE"),
            # line D changed in June: the January version is closed, a new one is current
            _line("D", "345", "RJ", "RJ", valid_to=JUN, row_hash="old"),
            _line("D", "345", "RJ", "RJ", valid_from=JUN, row_hash="new", length_km=Decimal("99")),
            # line E was removed from the register in June
            _line("E", "138", "PE", "PE", valid_to=JUN),
            Substation(
                station_id="BAS-5",
                valid_from=JAN,
                row_hash="s1",
                name="Sobradinho",
                voltage_kv=Decimal("500"),
                subsystem="NE",
                state="BA",
                latitude=Decimal("-9.43"),
                longitude=Decimal("-40.82"),
            ),
            Substation(
                station_id="SPS-2",
                valid_from=JAN,
                row_hash="s2",
                name="Santa Cruz",
                voltage_kv=Decimal("230"),
                subsystem="SE",
                state="SP",
            ),
            TransformerCapacity(
                equipment_code="TR1", valid_from=JAN, row_hash="t1", rated_mva=Decimal("150")
            ),
        ]
    )
    session.commit()


pytestmark = pytest.mark.usefixtures("grid_seed")


def _codes(body: dict[str, Any]) -> list[str]:
    return [item["equipment_code"] for item in body["items"]]


def test_lines_default_to_the_current_versions_sorted_by_voltage(client: TestClient) -> None:
    body = client.get("/v1/grid/lines").json()

    assert body["total"] == 4
    assert _codes(body) == ["A", "D", "B", "C"]
    assert [i["length_km"] for i in body["items"] if i["equipment_code"] == "D"] == [99.0]


def test_lines_filter_by_voltage_and_state(client: TestClient) -> None:
    assert _codes(client.get("/v1/grid/lines", params={"voltage_kv": 230}).json()) == ["B", "C"]
    assert _codes(client.get("/v1/grid/lines", params={"min_voltage_kv": 400}).json()) == ["A"]
    # a state matches either end of the line
    assert _codes(client.get("/v1/grid/lines", params={"state": "mg"}).json()) == ["A"]
    assert _codes(client.get("/v1/grid/lines", params={"state": "BA"}).json()) == ["A", "B"]


def test_lines_filter_by_subsystem(client: TestClient) -> None:
    body = client.get("/v1/grid/lines", params={"subsystem": "SE", "voltage_kv": 230}).json()
    assert _codes(body) == ["B", "C"]  # B ends in SE, C starts and ends there


def test_lines_as_of_shows_the_register_at_that_date(client: TestClient) -> None:
    january = client.get("/v1/grid/lines", params={"as_of": "2026-03-01"}).json()

    assert _codes(january) == ["A", "D", "B", "C", "E"]
    assert [i["length_km"] for i in january["items"] if i["equipment_code"] == "D"] == [120.5]
    assert january["as_of"] == "2026-03-01"
    before = client.get("/v1/grid/lines", params={"as_of": "2025-12-31"}).json()
    assert before["total"] == 0


def test_lines_paginate(client: TestClient) -> None:
    body = client.get("/v1/grid/lines", params={"page_size": 2, "page": 2}).json()

    assert body["total"] == 4 and _codes(body) == ["B", "C"]


def test_lines_reject_a_bad_state(client: TestClient) -> None:
    assert client.get("/v1/grid/lines", params={"state": "BAHIA"}).status_code == 422


def test_substations(client: TestClient) -> None:
    body = client.get("/v1/grid/substations", params={"state": "ba"}).json()

    assert body["total"] == 1
    assert body["items"][0]["name"] == "Sobradinho"
    assert body["items"][0]["latitude"] == -9.43
    assert client.get("/v1/grid/substations", params={"voltage_kv": 230}).json()["total"] == 1
    only_se = client.get("/v1/grid/substations", params={"subsystem": "SE"}).json()
    assert [i["station_id"] for i in only_se["items"]] == ["SPS-2"]
