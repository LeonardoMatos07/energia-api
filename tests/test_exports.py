import csv
import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.exporting.files import safe_cell, to_csv
from app.exporting.pdf import number, report_pdf, text
from app.models import HourlyCurtailment, MonthlyReport, Plant

pytestmark = pytest.mark.usefixtures("seed")

SEP = date(2026, 9, 1)
PARAMS = {"scope": "SIN", "source": "total", "start": "2026-07", "end": "2026-09"}


def rows(content: bytes) -> list[list[str]]:
    assert content.startswith("﻿".encode())
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


@pytest.fixture
def hourly(session: Session) -> None:
    session.add_all(
        HourlyCurtailment(
            subsystem="SIN",
            source="total",
            month=SEP,
            day_type=day,
            hour=hour,
            generation_mwh=Decimal("100.000"),
            curtailed_mwh=Decimal("10.000"),
            days=30,
        )
        for day in ("weekday", "weekend")
        for hour in range(24)
    )
    session.commit()


def test_the_analysis_comes_as_a_pdf(client: TestClient, hourly: None) -> None:
    r = client.get("/v1/exports/analysis.pdf", params=PARAMS)

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith('attachment; filename="spillway-analise-sin')
    assert r.headers["content-disposition"].endswith('2026-07_2026-09.pdf"')
    assert r.headers["cache-control"] == "no-store"
    assert r.content.startswith(b"%PDF-")
    assert len(r.content) > 2_000


def test_the_pdf_works_without_data_for_the_period(client: TestClient) -> None:
    r = client.get("/v1/exports/analysis.pdf", params={"scope": "S", "start": "2020-01"})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


ALL_SHEETS = [
    "Resumo",
    "Subsistemas por mês",
    "Estados por mês",
    "Empresas",
    "Pontos de conexão",
    "Perfil horário",
    "Usinas por mês",
]


def test_the_workbook_holds_every_table_in_one_file(client: TestClient, hourly: None) -> None:
    r = client.get("/v1/exports/analysis.xlsx", params=PARAMS)

    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.headers["content-disposition"].endswith('.xlsx"')
    book = load_workbook(io.BytesIO(r.content))
    assert book.sheetnames == ALL_SHEETS
    subsystems = list(book["Subsistemas por mês"].iter_rows(values_only=True))
    assert subsystems[0][:3] == ("Mês", "Subsistema", "Geração verificada (MWh)")
    assert {row[1] for row in subsystems[1:]} == {"NE", "SIN"}
    assert len(subsystems) == 1 + 6
    assert len(list(book["Perfil horário"].iter_rows())) == 1 + 48
    plants = list(book["Usinas por mês"].iter_rows(values_only=True))
    assert plants[0][:4] == ("Mês", "Usina", "Fonte", "UF")
    assert {row[1] for row in plants[1:]} >= {"Alpha I", "Bahia Sun", "Potiguar Winds"}
    assert "Alpha Cluster" not in {row[1] for row in plants[1:]}
    states = list(book["Estados por mês"].iter_rows(values_only=True))
    assert {row[1] for row in states[1:]} == {"BA"}


def test_the_csv_download_is_one_zip_with_every_table(client: TestClient, hourly: None) -> None:
    r = client.get("/v1/exports/analysis-csv.zip", params=PARAMS)

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].endswith('-csv.zip"')
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        assert archive.namelist() == [
            "01-summary.csv",
            "02-subsystems.csv",
            "03-states.csv",
            "04-operators.csv",
            "05-connection-points.csv",
            "06-hourly.csv",
            "07-plants.csv",
        ]
        summary = rows(archive.read("01-summary.csv"))
        subsystems = rows(archive.read("02-subsystems.csv"))
    assert ["Escopo", "SIN"] in summary
    assert ["Fonte", "Eólica e solar"] in summary
    assert ["Período", "07/2026 a 09/2026"] in summary
    assert [row[0] for row in subsystems[1:]] == ["2026-07"] * 2 + ["2026-08"] * 2 + ["2026-09"] * 2


def test_a_subsystem_filter_narrows_every_table(client: TestClient) -> None:
    r = client.get("/v1/exports/analysis-csv.zip", params={**PARAMS, "scope": "NE"})
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        subsystems = rows(archive.read("02-subsystems.csv"))
        plants = rows(archive.read("07-plants.csv"))
    assert {row[1] for row in subsystems[1:]} == {"NE"}
    assert {row[4] for row in plants[1:]} == {"NE"}


def test_the_rankings_are_not_cut_short(session: Session, client: TestClient) -> None:
    from app.models import PlantMonthlyCurtailment

    session.add_all(
        Plant(
            id=f"X{i}",
            kind="plant",
            name=f"Extra {i}",
            slug=f"extra-{i}",
            source="wind",
            state="BA",
            subsystem="NE",
            operator=f"Operator {i}",
            connection_point=f"Point {i}",
        )
        for i in range(250)
    )
    session.flush()
    session.add_all(
        PlantMonthlyCurtailment(
            plant_id=f"X{i}",
            month=SEP,
            verified_generation_mwh=Decimal("10"),
            curtailed_mwh=Decimal("1"),
            curtailed_rel_mwh=Decimal("0"),
            curtailed_cnf_mwh=Decimal("0"),
            curtailed_ene_mwh=Decimal("1"),
            curtailed_other_mwh=Decimal("0"),
            restricted_hours=Decimal("1"),
        )
        for i in range(250)
    )
    session.commit()

    r = client.get("/v1/exports/analysis-csv.zip", params=PARAMS)
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        operators = rows(archive.read("04-operators.csv"))
    assert len(operators) - 1 >= 250


def test_an_unknown_file_or_bad_parameter_is_refused(client: TestClient) -> None:
    assert client.get("/v1/exports/secrets.csv").status_code in (404, 422)
    assert client.get("/v1/exports/summary.csv").status_code in (404, 422)
    assert client.get("/v1/exports/analysis.pdf", params={"scope": "XX"}).status_code == 422
    assert client.get("/v1/exports/analysis.pdf", params={"start": "2026-13"}).status_code == 422
    backwards = {"start": "2026-09", "end": "2026-07"}
    assert client.get("/v1/exports/analysis.xlsx", params=backwards).status_code == 422
    assert client.get("/v1/exports/analysis-csv.zip", params=backwards).status_code == 422


def test_downloads_are_rate_limited(app_factory: object) -> None:
    from collections.abc import Callable

    make: Callable[..., TestClient] = app_factory  # type: ignore[assignment]
    limited = make(EXPORT_RATE_LIMIT_PER_MINUTE=2)
    codes = [limited.get("/v1/exports/analysis-csv.zip").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_a_published_report_comes_as_a_pdf(client: TestClient) -> None:
    r = client.get("/v1/reports/report-august-2026/pdf")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "spillway-relatorio-report-august-2026.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")


def test_a_draft_or_missing_report_has_no_pdf(client: TestClient) -> None:
    assert client.get("/v1/reports/report-september-2026/pdf").status_code == 404
    assert client.get("/v1/reports/nope/pdf").status_code == 404


def test_the_markdown_of_a_report_becomes_a_pdf(session: Session) -> None:
    content = """# Corte em alta

Texto com **negrito**, *itálico*, `código` e [um link](https://example.org).

## Lista

- um
- dois com <script>alert(1)</script>

1. primeiro
2. segundo

> citação importante

| Região | Corte |
|---|---|
| NE | 10 % |

```
bloco de código
```

---

Acentuação: coração, ação, não. Símbolo fora da fonte: ✓
"""
    pdf = report_pdf("Corte em alta", SEP, "Resumo", content)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 3_000


def test_a_report_with_a_ragged_table_and_empty_content_still_renders() -> None:
    assert report_pdf("Vazio", SEP, None, "").startswith(b"%PDF-")
    ragged = "| a | b | c |\n|---|---|---|\n| 1 |\n"
    assert report_pdf("Tabela", SEP, None, ragged).startswith(b"%PDF-")


def test_spreadsheet_cells_cannot_run_as_formulas() -> None:
    assert safe_cell('=HYPERLINK("http://evil")') == '\'=HYPERLINK("http://evil")'
    assert safe_cell("+55 71") == "'+55 71"
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell("Alpha") == "Alpha"
    assert safe_cell(-5) == -5
    assert safe_cell(None) is None


def test_csv_neutralises_formulas_and_blanks_missing_values() -> None:
    from app.exporting.data import Table

    table = Table("t", "T", ("a", "b"), (("=1+1", None), ("ok", 2.5)))
    assert rows(to_csv(table)) == [["a", "b"], ["'=1+1", ""], ["ok", "2.5"]]


def test_numbers_use_the_brazilian_format() -> None:
    assert number(1234567.891) == "1.234.568"
    assert number(1234.56, 1) == "1.234,6"
    assert number(None) == "—".encode("cp1252").decode("cp1252")
    assert text("a<b & c") == "a&lt;b &amp; c"


def test_a_report_with_html_in_the_markdown_is_not_rendered_as_markup(session: Session) -> None:
    row = MonthlyReport(
        month=SEP,
        title="x",
        slug="html-report",
        content="<b>raw</b> <img src=x onerror=alert(1)>",
        published_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    assert report_pdf(row.title, row.month, None, row.content).startswith(b"%PDF-")


def test_plants_are_not_needed_for_exports(session: Session) -> None:
    assert session.query(Plant).count() > 0
