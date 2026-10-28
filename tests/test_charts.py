import json

import pytest

from app.exporting.charts import chart_html, chart_text, parse_chart
from app.exporting.email_content import markdown_html, markdown_text, report_blocks
from app.exporting.pdf import report_pdf
from tests.test_exports import SEP


def spec(kind: str = "bar", series: int = 1) -> dict[str, object]:
    return {
        "type": kind,
        "title": "Energia cortada <b>por mês</b>",
        "unit": "GWh",
        "labels": ["jul/26", "ago/26", "set/26"],
        "series": [
            {"name": f"Série {i}", "values": [1.5 * (i + 1), 3.0, 4.5]} for i in range(series)
        ],
    }


def fenced(payload: dict[str, object]) -> str:
    return (
        "Texto antes.\n\n```chart\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n\nDepois."
    )


def test_a_valid_spec_is_parsed_and_an_invalid_one_is_not() -> None:
    assert parse_chart(json.dumps(spec())) is not None
    assert parse_chart("not json") is None
    broken = spec()
    broken["series"] = [{"name": "x", "values": [1.0]}]
    assert parse_chart(json.dumps(broken)) is None
    assert parse_chart(json.dumps({**spec(), "type": "pie"})) is None


@pytest.mark.parametrize("kind", ["bar", "stacked-bar", "line"])
def test_every_kind_renders_in_the_pdf(kind: str) -> None:
    pdf = report_pdf("Relatório", SEP, None, fenced(spec(kind, series=2)))
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 2_500


def test_a_chart_with_flat_or_empty_values_still_renders() -> None:
    flat = spec()
    flat["series"] = [{"name": "x", "values": [0.0, 0.0, 0.0]}]
    assert report_pdf("Relatório", SEP, None, fenced(flat)).startswith(b"%PDF-")


def test_the_email_draws_bars_with_table_cells_and_escapes_the_title() -> None:
    html = markdown_html(fenced(spec("stacked-bar", series=2)))

    assert "<table" in html
    assert "background:#0d9488" in html
    assert "background:#f59e0b" in html
    assert "Série 0" in html
    assert "Energia cortada &lt;b&gt;por mês&lt;/b&gt;" in html
    assert "<b>por mês</b>" not in html
    assert "<script" not in html and "<img" not in html and "```" not in html


def test_the_widest_bar_is_the_full_width_and_the_others_are_proportional() -> None:
    parsed = parse_chart(json.dumps(spec()))
    assert parsed is not None
    html = chart_html(parsed)
    assert "width:100.0%" in html
    assert "width:33.3%" in html


def test_the_text_version_lists_the_values() -> None:
    parsed = parse_chart(json.dumps(spec()))
    assert parsed is not None
    text = chart_text(parsed)
    assert text.startswith("Energia cortada <b>por mês</b>:")
    assert "- jul/26: 1,5 GWh" in text
    assert "- set/26: 4,5 GWh" in text
    assert "chart" not in markdown_text(fenced(spec())).splitlines()[0]
    assert "- ago/26: 3,0 GWh" in markdown_text(fenced(spec()))


def test_a_broken_chart_block_is_shown_as_code_not_dropped() -> None:
    content = "```chart\n{nope\n```\n"
    assert "{nope" in markdown_html(content)
    assert "{nope" in markdown_text(content)


def test_other_code_blocks_are_untouched() -> None:
    html = markdown_html("```python\nprint(1)\n```\n")
    assert "<pre" in html and "print(1)" in html


def test_chart_text_stays_inside_the_email_text_budget() -> None:
    many = {**spec(), "labels": [f"m{i}" for i in range(48)]}
    many["series"] = [{"name": "s", "values": [float(i) for i in range(48)]}]
    _, text = report_blocks(fenced(many), "https://example.org/r")
    assert len(text) < 3_400
