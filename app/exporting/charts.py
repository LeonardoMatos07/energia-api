"""Charts in a report: one description, drawn three ways.

A report is Markdown. A chart is a fenced block whose language is `chart` and whose body is JSON:

    {"type": "bar", "title": "...", "unit": "GWh", "labels": ["jul/26", ...],
     "series": [{"name": "Energia cortada", "values": [3.0, ...]}]}

`type` is `bar`, `stacked-bar` or `line`. The site draws it with Recharts, the PDF with
reportlab (`chart_drawing`) and the e-mail, where nothing runs, with bars made of table cells
(`chart_html`) and a plain-text summary (`chart_text`).
"""

import math
from html import escape
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors

COLORS = ("#0d9488", "#f59e0b", "#2a78d6", "#6250d6", "#e87ba4", "#cc8200")
MAX_LABELS = 48
MAX_SERIES = 6
INK = "#34495e"
MUTED = "#52667a"
GRID = "#dce6e3"


class Series(BaseModel):
    name: str = Field(max_length=80)
    values: list[float] = Field(max_length=MAX_LABELS)


class ChartSpec(BaseModel):
    type: Literal["bar", "stacked-bar", "line"] = "bar"
    title: str = Field(max_length=160)
    unit: str = Field(default="", max_length=20)
    labels: list[str] = Field(min_length=1, max_length=MAX_LABELS)
    series: list[Series] = Field(min_length=1, max_length=MAX_SERIES)

    @model_validator(mode="after")
    def _same_length(self) -> "ChartSpec":
        if any(len(s.values) != len(self.labels) for s in self.series):
            raise ValueError("every series needs one value per label")
        return self


def parse_chart(raw: str) -> ChartSpec | None:
    """The spec, or None when the block is not a valid chart (it is then shown as code)."""
    try:
        return ChartSpec.model_validate_json(raw)
    except ValidationError:
        return None


def format_number(value: float, digits: int = 1) -> str:
    """Brazilian format: 1.234,5."""
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _nice_step(raw: float) -> float:
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if raw <= factor * magnitude:
            return float(factor * magnitude)
    return float(10 * magnitude)


def _top(spec: ChartSpec) -> float:
    if spec.type == "stacked-bar":
        totals = [sum(s.values[i] for s in spec.series) for i in range(len(spec.labels))]
    else:
        totals = [v for s in spec.series for v in s.values]
    return max([*totals, 0.0])


# --- PDF ---------------------------------------------------------------------------------


def chart_drawing(spec: ChartSpec, width: float, height: float = 190) -> Drawing:
    drawing = Drawing(width, height)
    peak = _top(spec)
    step = _nice_step(peak / 5 if peak > 0 else 1.0)
    top = step * (int(peak / step) + 1)
    legend = len(spec.series) > 1
    plot_top = height - (20 if legend else 6)

    if spec.type == "line":
        plot = LinePlot()
        plot.data = [[(i, v) for i, v in enumerate(s.values)] for s in spec.series]
        plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = 0, max(len(spec.labels) - 1, 1)
        plot.xValueAxis.valueSteps = list(range(len(spec.labels)))
        plot.xValueAxis.labelTextFormat = lambda i: (
            spec.labels[int(i)] if 0 <= int(i) < len(spec.labels) else ""
        )
        for index in range(len(spec.series)):
            plot.lines[index].strokeColor = colors.HexColor(COLORS[index])
            plot.lines[index].strokeWidth = 1.8
        axis_x, axis_y = plot.xValueAxis, plot.yValueAxis
    else:
        plot = VerticalBarChart()
        plot.data = [s.values for s in spec.series]
        plot.categoryAxis.categoryNames = spec.labels
        plot.categoryAxis.labels.boxAnchor = "n"
        plot.categoryAxis.labels.dy = -3
        plot.groupSpacing = 6
        plot.barSpacing = 1
        plot.bars.strokeColor = None
        if spec.type == "stacked-bar":
            plot.categoryAxis.style = "stacked"
        for index in range(len(spec.series)):
            plot.bars[index].fillColor = colors.HexColor(COLORS[index])
        axis_x, axis_y = plot.categoryAxis, plot.valueAxis

    plot.x, plot.y = 44, 26
    plot.width, plot.height = width - 60, plot_top - 26
    axis_y.valueMin, axis_y.valueMax, axis_y.valueStep = 0, top, step
    axis_y.labelTextFormat = lambda value: format_number(value, 0 if step >= 1 else 1)
    axis_y.visibleGrid = True
    axis_y.gridStrokeColor = colors.HexColor(GRID)
    axis_x.labels.fontName = axis_y.labels.fontName = "Helvetica"
    axis_x.labels.fontSize = axis_y.labels.fontSize = 7
    drawing.add(plot)

    if legend:
        x = 44.0
        for index, series in enumerate(spec.series):
            drawing.add(
                String(x, height - 12, f"■ {series.name}", fontName="Helvetica", fontSize=8,
                       fillColor=colors.HexColor(COLORS[index]))
            )  # fmt: skip
            x += 14 + 5 * len(series.name)
    return drawing


# --- E-mail ------------------------------------------------------------------------------


def chart_html(spec: ChartSpec) -> str:
    """Horizontal bars as table cells: works in every mail client, with no image to host."""
    peak = _top(spec) or 1.0
    unit = f" {escape(spec.unit)}" if spec.unit else ""
    legend = ""
    if len(spec.series) > 1:
        legend = (
            '<p style="margin:0 0 6px 0;font-size:12px;line-height:16px;color:'
            + MUTED
            + ';">'
            + " &nbsp; ".join(
                f'<span style="color:{COLORS[i]};">&#9632;</span> {escape(s.name)}'
                for i, s in enumerate(spec.series)
            )
            + "</p>"
        )

    def cell(value: float, color: str, height: int) -> str:
        width = max(value / peak * 100, 0.5)
        return (
            f'<td style="width:{width:.1f}%;background:{color};height:{height}px;'
            'font-size:1px;line-height:1px;">&nbsp;</td>'
        )

    def table(inner: str) -> str:
        return (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f"{inner}</table>"
        )

    rows = []
    for index, label in enumerate(spec.labels):
        values = [s.values[index] for s in spec.series]
        if spec.type == "stacked-bar":
            bar = table(
                "<tr>"
                + "".join(cell(v, COLORS[i], 14) for i, v in enumerate(values) if v > 0)
                + "<td></td></tr>"
            )
            shown = format_number(sum(values))
        else:
            bar = table(
                "".join(
                    f"<tr>{cell(v, COLORS[i], 14 if len(values) == 1 else 8)}<td></td></tr>"
                    for i, v in enumerate(values)
                )
            )
            shown = " · ".join(format_number(v) for v in values)
        rows.append(
            '<tr><td style="width:30%;padding:4px 8px 4px 0;font-size:13px;line-height:18px;'
            f'color:{INK};">{escape(label)}</td>'
            f'<td style="padding:4px 0;">{bar}</td>'
            '<td style="width:22%;padding:4px 0 4px 8px;font-size:13px;line-height:18px;'
            f'color:{INK};text-align:right;white-space:nowrap;">{shown}{unit}</td></tr>'
        )
    return (
        '<div style="margin:0 0 16px 0;">'
        '<p style="margin:0 0 6px 0;font-size:15px;line-height:20px;font-weight:700;'
        'color:#0e1b2c;">'
        f"{escape(spec.title)}</p>{legend}"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + "".join(rows)
        + "</table></div>"
    )


def chart_text(spec: ChartSpec) -> str:
    unit = f" {spec.unit}" if spec.unit else ""
    head = f"{spec.title}:"
    lines = []
    for index, label in enumerate(spec.labels):
        values = ", ".join(
            (f"{s.name} " if len(spec.series) > 1 else "")
            + f"{format_number(s.values[index])}{unit}"
            for s in spec.series
        )
        lines.append(f"- {label}: {values}")
    return head + "\n" + "\n".join(lines)
