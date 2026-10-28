"""PDF files: the analyses and the monthly reports.

reportlab's built-in fonts cover Western European text, which is all this site writes; anything
outside it is replaced instead of printed as a box.
"""

import io
import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from xml.sax.saxutils import escape

from markdown_it import MarkdownIt
from markdown_it.token import Token
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

from app.exporting.data import SOURCE_LABELS, Analysis, Cell, tables
from app.exporting.data import Table as DataTable

INK = colors.HexColor("#0e1b2c")
INK_2 = colors.HexColor("#34495e")
MUTED = colors.HexColor("#52667a")
ACCENT = colors.HexColor("#0f766e")
SOFT = colors.HexColor("#e9f1ef")
LINE = colors.HexColor("#c5d5d1")
AMBER = colors.HexColor("#f59e0b")

PAGE_MARGIN = 18 * mm
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN
TOP_ROWS = 15
HOURS = 24

_styles = getSampleStyleSheet()
BODY = ParagraphStyle(
    "body", parent=_styles["BodyText"], fontName="Helvetica", fontSize=10, leading=15,
    textColor=INK_2, spaceAfter=6, alignment=TA_LEFT,
)  # fmt: skip
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=11, textColor=MUTED)
TITLE = ParagraphStyle(
    "title", parent=BODY, fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=INK,
    spaceAfter=4,
)  # fmt: skip
KICKER = ParagraphStyle(
    "kicker", parent=BODY, fontName="Helvetica-Bold", fontSize=9, textColor=ACCENT, spaceAfter=2
)
H1 = ParagraphStyle(
    "h1", parent=TITLE, fontSize=17, leading=21, spaceBefore=14, spaceAfter=6
)  # fmt: skip
H2 = ParagraphStyle("h2", parent=TITLE, fontSize=14, leading=18, spaceBefore=12, spaceAfter=5)
H3 = ParagraphStyle("h3", parent=TITLE, fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=4)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8, leading=10, spaceAfter=0)
CELL_HEAD = ParagraphStyle(
    "cellhead", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white
)
CODE = ParagraphStyle(
    "code", parent=BODY, fontName="Courier", fontSize=8.5, leading=11, backColor=SOFT,
    borderPadding=5, spaceBefore=4, spaceAfter=8,
)  # fmt: skip


def text(value: object) -> str:
    """Escaped for reportlab's mini-markup, limited to what the built-in font can print."""
    plain = "" if value is None else str(value)
    return escape(plain.encode("cp1252", errors="replace").decode("cp1252"))


def number(value: Cell, digits: int = 0) -> str:
    """Brazilian format: 1.234,5."""
    if value is None:
        return "—".encode("cp1252", errors="replace").decode("cp1252")
    if isinstance(value, str):
        return value
    formatted = f"{value:,.{digits}f}"
    return formatted.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _footer(title: str) -> Callable[[Canvas, Any], None]:
    stamp = datetime.now(UTC).strftime("%d/%m/%Y")

    def draw(canvas: Canvas, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(PAGE_MARGIN, 10 * mm, text(f"Spillway · {title}"))
        canvas.drawRightString(
            A4[0] - PAGE_MARGIN, 10 * mm, f"Gerado em {stamp} · página {doc.page}"
        )
        canvas.setStrokeColor(LINE)
        canvas.line(PAGE_MARGIN, 14 * mm, A4[0] - PAGE_MARGIN, 14 * mm)
        canvas.restoreState()

    return draw


def _build(title: str, story: list[Flowable], author: str = "Spillway") -> bytes:
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=20 * mm,
        title=text(title),
        author=author,
    )
    footer = _footer(title)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def _grid(
    header: list[str],
    rows: list[list[str]],
    widths: list[float],
    numeric_from: int = 1,
) -> Table:
    data: list[list[Any]] = [[Paragraph(text(h), CELL_HEAD) for h in header]]
    data += [[Paragraph(text(cell), CELL) for cell in row] for row in rows]
    grid = Table(data, colWidths=widths, repeatRows=1)
    grid.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return grid


def _cover(kicker: str, title: str, subtitle: str) -> list[Flowable]:
    return [
        Paragraph(text(kicker.upper()), KICKER),
        Paragraph(text(title), TITLE),
        Paragraph(text(subtitle), SMALL),
        HRFlowable(width="100%", thickness=1, color=ACCENT, spaceBefore=6, spaceAfter=10),
    ]


def _kpis(data: Analysis) -> Table:
    s = data.summary
    cells = [
        ("Energia cortada", f"{number(s.curtailed_mwh)} MWh"),
        ("Corte sobre a geração", f"{number(s.curtailment_pct, 1)} %"),
        ("Perda estimada", f"R$ {number(s.estimated_loss_brl)}"),
        ("Usinas afetadas", number(s.affected_plants)),
    ]
    width = CONTENT_WIDTH / len(cells)
    label = ParagraphStyle("kl", parent=SMALL, spaceAfter=2)
    value = ParagraphStyle("kv", parent=BODY, fontName="Helvetica-Bold", fontSize=12.5, leading=15)
    grid = Table(
        [[[Paragraph(text(lbl), label), Paragraph(text(val), value)] for lbl, val in cells]],
        colWidths=[width] * len(cells),
    )
    grid.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return grid


def _reasons(data: Analysis) -> Table:
    s = data.summary
    total = s.curtailed_mwh or 0
    items = [
        ("Confiabilidade elétrica (REL)", s.curtailed_rel_mwh),
        ("Indisponibilidade externa (CNF)", s.curtailed_cnf_mwh),
        ("Razão energética (ENE)", s.curtailed_ene_mwh),
        ("Outros motivos", s.curtailed_other_mwh),
    ]
    rows = [
        [label, number(mwh), number(100 * mwh / total if total else None, 1) + " %"]
        for label, mwh in items
    ]
    return _grid(
        ["Motivo do corte", "Energia (MWh)", "Parcela"],
        rows,
        [CONTENT_WIDTH * 0.55, CONTENT_WIDTH * 0.25, CONTENT_WIDTH * 0.2],
    )


def _ranking(table: DataTable) -> Table:
    shown = table.rows[:TOP_ROWS]
    with_state = any(r[2] for r in shown)
    header = ["#", table.headers[1], "UF", "Usinas afetadas", "Cortada (MWh)", "Corte (%)"]
    header.append("Compensável (MWh)")
    widths = [0.05, 0.33, 0.06, 0.12, 0.16, 0.11, 0.17]
    rows = [
        [
            number(r[0]),
            str(r[1]),
            str(r[2] or ""),
            number(r[4]),
            number(r[6]),
            number(r[7], 1),
            number(r[9]),
        ]
        for r in shown
    ]
    if not with_state:
        header.pop(2)
        widths[1] += widths.pop(2)
        rows = [row[:2] + row[3:] for row in rows]
    return _grid(header, rows, [CONTENT_WIDTH * share for share in widths])


def _nice_step(raw: float) -> float:
    """1, 2 or 5 times a power of ten, so the axis counts in round numbers."""
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if raw <= factor * magnitude:
            return float(factor * magnitude)
    return float(10 * magnitude)


def _hourly_chart(data: Analysis) -> Drawing:
    width, height = CONTENT_WIDTH, 170
    drawing = Drawing(width, height)
    series: dict[str, list[tuple[int, float]]] = {"weekday": [], "weekend": []}
    for point in data.hourly.points:
        series[point.day_type].append((point.hour, point.avg_curtailed_mwh))
    plot = LinePlot()
    plot.x, plot.y = 38, 28
    plot.width, plot.height = width - 54, height - 48
    plot.data = [sorted(series["weekday"]), sorted(series["weekend"])]
    plot.lines[0].strokeColor = ACCENT
    plot.lines[1].strokeColor = AMBER
    plot.lines[0].strokeWidth = plot.lines[1].strokeWidth = 1.8
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = 0, HOURS - 1
    plot.xValueAxis.valueSteps = list(range(0, HOURS, 3))
    peak = max((value for line in plot.data for _, value in line), default=0.0)
    step = _nice_step(peak / 5 if peak > 0 else 1.0)
    plot.yValueAxis.valueMin = 0
    plot.yValueAxis.valueMax = step * (int(peak / step) + 1)
    plot.yValueAxis.valueStep = step
    plot.yValueAxis.labelTextFormat = lambda value: number(value)
    plot.xValueAxis.labels.fontName = plot.yValueAxis.labels.fontName = "Helvetica"
    plot.xValueAxis.labels.fontSize = plot.yValueAxis.labels.fontSize = 7
    plot.yValueAxis.gridStrokeColor = LINE
    plot.yValueAxis.visibleGrid = True
    drawing.add(plot)
    drawing.add(
        String(
            38,
            height - 10,
            "Corte médio (MWh) por hora do dia",
            fontSize=8,
            fontName="Helvetica",
            fillColor=MUTED,
        )
    )
    drawing.add(
        String(
            width - 150,
            height - 10,
            "— Dia útil",
            fontSize=8,
            fontName="Helvetica",
            fillColor=ACCENT,
        )
    )
    drawing.add(
        String(
            width - 78,
            height - 10,
            "— Fim de semana",
            fontSize=8,
            fontName="Helvetica",
            fillColor=AMBER,
        )
    )
    return drawing


def analysis_pdf(data: Analysis) -> bytes:
    all_tables = tables(data)
    subtitle = f"Escopo {data.scope} · {SOURCE_LABELS[data.source]} · período {data.period_label}"
    story: list[Flowable] = _cover("Análises de curtailment", "Análise de curtailment", subtitle)
    story += [_kpis(data), Spacer(1, 10)]
    story += [Paragraph("Motivos do corte", H2), _reasons(data)]
    story += [
        Paragraph(
            "Confiabilidade elétrica e indisponibilidade externa são os cortes que podem dar "
            "direito a ressarcimento; razão energética e outros não.",
            SMALL,
        )
    ]
    story += [Paragraph(text("Empresas que mais perdem"), H2), _ranking(all_tables["operators"])]
    story += [
        Paragraph("Pontos de conexão que mais perdem", H2),
        _ranking(all_tables["connection-points"]),
    ]
    if data.hourly.points:
        story += [
            KeepTogether(
                [
                    Paragraph("Em que horas a energia sobra", H2),
                    _hourly_chart(data),
                    Paragraph(text(all_tables["hourly"].note or ""), SMALL),
                ]
            )
        ]
    story += [
        Spacer(1, 8),
        Paragraph(
            "Dados do ONS e da CCEE tratados pelo Spillway. Os valores completos estão disponíveis "
            "em CSV e Excel no site.",
            SMALL,
        ),
    ]
    return _build(f"Análise de curtailment, {data.period_label}", story)


# --- Markdown reports ---------------------------------------------------------------------

_markdown = MarkdownIt("commonmark", {"html": False}).enable("table")
HEADINGS = {"h1": H1, "h2": H2, "h3": H3, "h4": H3, "h5": H3, "h6": H3}


def _inline(children: list[Token] | None) -> str:
    out: list[str] = []
    for token in children or []:
        match token.type:
            case "text":
                out.append(text(token.content))
            case "code_inline":
                out.append(f'<font face="Courier">{text(token.content)}</font>')
            case "softbreak":
                out.append(" ")
            case "hardbreak":
                out.append("<br/>")
            case "strong_open":
                out.append("<b>")
            case "strong_close":
                out.append("</b>")
            case "em_open":
                out.append("<i>")
            case "em_close":
                out.append("</i>")
            case "link_open":
                href = escape(str(token.attrGet("href") or ""), {'"': "&quot;"})
                out.append(f'<link href="{href}" color="#0f766e"><u>')
            case "link_close":
                out.append("</u></link>")
            case "image":
                out.append(text(token.content))
    return "".join(out)


class _Blocks:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.position = 0

    def parse(self, until: str | None = None) -> list[Flowable]:
        out: list[Flowable] = []
        while self.position < len(self.tokens):
            token = self.tokens[self.position]
            if until is not None and token.type == until:
                break
            self.position += 1
            out.extend(self._block(token))
        return out

    def _block(self, token: Token) -> list[Flowable]:
        kind = token.type
        if kind == "heading_open":
            inline = self.tokens[self.position]
            self.position += 2
            return [Paragraph(_inline(inline.children), HEADINGS.get(token.tag, H3))]
        if kind == "paragraph_open":
            inline = self.tokens[self.position]
            self.position += 2
            return [Paragraph(_inline(inline.children), BODY)]
        if kind in ("bullet_list_open", "ordered_list_open"):
            return [self._list(kind == "ordered_list_open")]
        if kind == "blockquote_open":
            inner = self.parse(until="blockquote_close")
            self.position += 1
            box = Table([[inner]], colWidths=[CONTENT_WIDTH - 10])
            box.setStyle(
                TableStyle(
                    [
                        ("LINEBEFORE", (0, 0), (0, -1), 2, ACCENT),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ]
                )
            )
            return [box]
        if kind in ("fence", "code_block"):
            return [Paragraph(text(token.content.rstrip()).replace("\n", "<br/>"), CODE)]
        if kind == "hr":
            return [
                HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=6, spaceAfter=6)
            ]
        if kind == "table_open":
            return [self._table()]
        return []

    def _list(self, ordered: bool) -> Flowable:
        items: list[ListItem] = []
        while self.position < len(self.tokens):
            token = self.tokens[self.position]
            self.position += 1
            if token.type in ("bullet_list_close", "ordered_list_close"):
                break
            if token.type == "list_item_open":
                items.append(ListItem(self.parse(until="list_item_close"), leftIndent=14))
                self.position += 1
        return ListFlowable(
            items,
            bulletType="1" if ordered else "bullet",
            bulletFontSize=9,
            leftIndent=14,
            bulletColor=ACCENT,
        )

    def _table(self) -> Flowable:
        header: list[str] = []
        rows: list[list[str]] = []
        in_head = False
        current: list[str] = []
        while self.position < len(self.tokens):
            token = self.tokens[self.position]
            self.position += 1
            if token.type == "table_close":
                break
            if token.type == "thead_open":
                in_head = True
            elif token.type == "thead_close":
                in_head = False
            elif token.type == "tr_open":
                current = []
            elif token.type == "tr_close":
                if in_head:
                    header = current
                else:
                    rows.append(current)
            elif token.type == "inline":
                current.append(_inline(token.children))
        columns = max([len(header)] + [len(r) for r in rows] + [1])

        def pad(row: list[str]) -> list[str]:
            return row + [""] * (columns - len(row))

        data: list[list[Any]] = [[Paragraph(c, CELL_HEAD) for c in pad(header)]]
        data += [[Paragraph(c, CELL) for c in pad(r)] for r in rows]
        grid = Table(data, colWidths=[CONTENT_WIDTH / columns] * columns, repeatRows=1)
        grid.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), INK),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return grid


def report_pdf(title: str, month: date, summary: str | None, content: str) -> bytes:
    story: list[Flowable] = _cover(
        f"Relatório mensal · {month:%m/%Y}", title, summary or "Relatório mensal do Spillway"
    )
    story += _Blocks(_markdown.parse(content)).parse()
    return _build(title, story)
