import csv
import io
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


def test_the_workbook_has_one_sheet_per_table(client: TestClient, hourly: None) -> None:
    r = client.get("/v1/exports/analysis.xlsx", params=PARAMS)

    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    book = load_workbook(io.BytesIO(r.content))
    assert book.sheetnames == [
        "Resumo",
        "Subsistemas por mês",
        "Empresas",
        "Pontos de conexão",
        "Perfil horário",
    ]
    subsystems = list(book["Subsistemas por mês"].iter_rows(values_only=True))
    assert subsystems[0][:3] == ("Mês", "Subsistema", "Geração verificada (MWh)")
    assert {row[1] for row in subsystems[1:]} == {"NE", "SIN"}
    assert len(subsystems) == 1 + 6
    assert len(list(book["Perfil horário"].iter_rows())) == 1 + 48


def test_csv_downloads_one_table(client: TestClient) -> None:
    r = client.get("/v1/exports/subsystems.csv", params=PARAMS)

    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert "subsystems.csv" in r.headers["content-disposition"]
    table = rows(r.content)
    assert table[0][:3] == ["Mês", "Subsistema", "Geração verificada (MWh)"]
    assert [row[0] for row in table[1:]] == ["2026-07"] * 2 + ["2026-08"] * 2 + ["2026-09"] * 2
    assert {row[1] for row in table[1:]} == {"NE", "SIN"}


@pytest.mark.parametrize("dataset", ["summary", "operators", "connection-points", "hourly"])
def test_every_dataset_is_available_as_csv(client: TestClient, dataset: str) -> None:
    r = client.get(f"/v1/exports/{dataset}.csv", params=PARAMS)
    assert r.status_code == 200
    assert len(rows(r.content)) >= 1


def test_the_summary_csv_reads_like_the_screen(client: TestClient) -> None:
    table = rows(client.get("/v1/exports/summary.csv", params=PARAMS).content)
    assert table[0] == ["Indicador", "Valor"]
    assert ["Escopo", "SIN"] in table
    assert ["Fonte", "Eólica e solar"] in table
    assert ["Período", "07/2026 a 09/2026"] in table


def test_a_subsystem_filter_narrows_the_series(client: TestClient) -> None:
    table = rows(client.get("/v1/exports/subsystems.csv", params={**PARAMS, "scope": "NE"}).content)
    assert {row[1] for row in table[1:]} == {"NE"}


def test_an_unknown_dataset_or_bad_parameter_is_refused(client: TestClient) -> None:
    assert client.get("/v1/exports/secrets.csv").status_code == 422
    assert client.get("/v1/exports/analysis.pdf", params={"scope": "XX"}).status_code == 422
    assert client.get("/v1/exports/analysis.pdf", params={"start": "2026-13"}).status_code == 422
    backwards = {"start": "2026-09", "end": "2026-07"}
    assert client.get("/v1/exports/analysis.xlsx", params=backwards).status_code == 422


def test_downloads_are_rate_limited(app_factory: object) -> None:
    from collections.abc import Callable

    make: Callable[..., TestClient] = app_factory  # type: ignore[assignment]
    limited = make(EXPORT_RATE_LIMIT_PER_MINUTE=2)
    codes = [limited.get("/v1/exports/summary.csv").status_code for _ in range(3)]
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
