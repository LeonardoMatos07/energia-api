"""The report and the analyses written for the body of an e-mail.

Each function returns `(html, text)`. The HTML is built for mail clients: inline styles, tables
for layout, nothing that needs a stylesheet or a script. Raw HTML inside the report's Markdown is
not passed through, and the parser itself refuses javascript: and similar links.
"""

import re
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.exporting.charts import chart_html, chart_text, parse_chart
from app.exporting.data import Analysis, tables
from app.exporting.pdf import number

TEXT_LIMIT = 2_800
HTML_LIMIT = 150_000
TOP_ROWS = 5

_markdown = MarkdownIt("commonmark", {"html": False}).enable("table")


_renderer: Any = _markdown.renderer
_default_fence = _renderer.rules["fence"]


def _render_fence(tokens: Any, idx: int, options: Any, env: Any) -> str:
    """A `chart` block becomes bars in table cells; any other code block renders as usual."""
    token = tokens[idx]
    if token.info.strip() == "chart":
        spec = parse_chart(token.content)
        if spec is not None:
            return chart_html(spec)
    return str(_default_fence(tokens, idx, options, env))


_renderer.rules["fence"] = _render_fence

_STYLES = {
    "h1": "margin:24px 0 10px 0;font-size:20px;line-height:26px;color:#0e1b2c;",
    "h2": "margin:22px 0 8px 0;font-size:18px;line-height:24px;color:#0e1b2c;",
    "h3": "margin:18px 0 6px 0;font-size:16px;line-height:22px;color:#0e1b2c;",
    "h4": "margin:16px 0 6px 0;font-size:15px;line-height:21px;color:#0e1b2c;",
    "p": "margin:0 0 14px 0;",
    "ul": "margin:0 0 14px 0;padding-left:22px;",
    "ol": "margin:0 0 14px 0;padding-left:22px;",
    "li": "margin:0 0 6px 0;",
    "blockquote": "margin:0 0 14px 0;padding:2px 0 2px 14px;"
    "border-left:3px solid #0f766e;color:#52667a;",
    "pre": "margin:0 0 14px 0;padding:10px 12px;background:#e9f1ef;border-radius:6px;"
    "font-family:'Courier New',Courier,monospace;font-size:13px;line-height:18px;"
    "white-space:pre-wrap;",
    "code": "font-family:'Courier New',Courier,monospace;font-size:14px;background:#e9f1ef;",
    "table": "width:100%;margin:0 0 16px 0;border-collapse:collapse;font-size:14px;",
    "th": "padding:8px;background:#0e1b2c;color:#ffffff;text-align:left;",
    "td": "padding:8px;border-bottom:1px solid #dce6e3;",
    "a": "color:#0f766e;",
    "hr": "border:0;border-top:1px solid #dce6e3;margin:20px 0;",
    "img": "max-width:100%;height:auto;",
}
_TAG = re.compile(r"<(h[1-6]|p|ul|ol|li|blockquote|pre|code|table|th|td|a|hr|img)(\s[^>]*)?(/?)>")


def _style_tag(match: re.Match[str]) -> str:
    tag, attributes, closing = match.group(1), match.group(2) or "", match.group(3)
    style = _STYLES.get(tag, _STYLES["h4"] if tag.startswith("h") else "")
    if tag in ("th", "td") and "style=" in attributes:
        attributes = re.sub(
            r'style="([^"]*)"', lambda m: f'style="{style}{m.group(1)}"', attributes
        )
        return f"<{tag}{attributes}{closing}>"
    return f'<{tag}{attributes} style="{style}"{closing}>'


def markdown_html(content: str) -> str:
    rendered = _markdown.render(content)
    return _TAG.sub(_style_tag, rendered)


def _inline_text(children: list[Token] | None) -> str:
    out: list[str] = []
    for token in children or []:
        if token.type in ("text", "code_inline"):
            out.append(token.content)
        elif token.type in ("softbreak", "hardbreak"):
            out.append(" ")
        elif token.type == "link_open":
            continue
        elif token.type == "image":
            out.append(token.content)
    return "".join(out)


def markdown_text(content: str, limit: int = TEXT_LIMIT) -> str:
    """The Markdown as plain paragraphs, cut at a paragraph so the text alternative of the e-mail
    stays within what Relay accepts."""
    paragraphs: list[str] = []
    prefix = ""
    for token in _markdown.parse(content):
        if token.type == "list_item_open":
            prefix = "- "
        elif token.type == "inline":
            line = _inline_text(token.children).strip()
            if line:
                paragraphs.append(prefix + line)
            prefix = ""
        elif token.type == "fence" and token.info.strip() == "chart":
            spec = parse_chart(token.content)
            if spec is not None:
                paragraphs.append(chart_text(spec))
            elif token.content.strip():
                paragraphs.append(token.content.strip())
        elif token.type in ("fence", "code_block") and token.content.strip():
            paragraphs.append(token.content.strip())
    out: list[str] = []
    size = 0
    for paragraph in paragraphs:
        size += len(paragraph) + 2
        if size > limit:
            out.append("[...]")
            break
        out.append(paragraph)
    return "\n\n".join(out)


def report_blocks(content: str, url: str) -> tuple[str, str]:
    html = markdown_html(content)
    text = markdown_text(content)
    if len(html) > HTML_LIMIT:
        html = (
            f'<p style="{_STYLES["p"]}">Este relatório é longo demais para um e-mail. '
            "Leia a versão completa no site.</p>"
        )
    return html, f"{text}\n\nRelatório completo: {url}"


# --- Analyses ----------------------------------------------------------------------------


def _kpi_html(label: str, value: str) -> str:
    return (
        '<td align="center" style="padding:12px 6px;background:#e9f1ef;border:2px solid #ffffff;">'
        f'<div style="font-size:12px;line-height:16px;color:#52667a;">{escape(label)}</div>'
        f'<div style="font-size:17px;line-height:22px;font-weight:700;color:#0e1b2c;">'
        f"{escape(value)}</div></td>"
    )


def _rows_html(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f'<th style="{_STYLES["th"]}">{escape(h)}</th>' for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td style="{_STYLES["td"]}">{escape(c)}</td>' for c in row) + "</tr>"
        for row in rows
    )
    return f'<table style="{_STYLES["table"]}"><tr>{head}</tr>{body}</table>'


def analysis_blocks(data: Analysis, url: str) -> tuple[str, str]:
    s = data.summary
    if s.months == 0:
        message = "Os números detalhados desta análise estão no site."
        return f'<p style="{_STYLES["p"]}">{message}</p>', f"{message}\n\nVeja em: {url}"

    kpis = [
        ("Energia cortada", f"{number(s.curtailed_mwh)} MWh"),
        ("Corte sobre a geração", f"{number(s.curtailment_pct, 1)} %"),
        ("Perda estimada", f"R$ {number(s.estimated_loss_brl)}"),
        ("Usinas afetadas", number(s.affected_plants)),
    ]
    reasons = [
        ("Confiabilidade elétrica (REL)", s.curtailed_rel_mwh),
        ("Indisponibilidade externa (CNF)", s.curtailed_cnf_mwh),
        ("Razão energética (ENE)", s.curtailed_ene_mwh),
        ("Outros motivos", s.curtailed_other_mwh),
    ]
    total = s.curtailed_mwh or 0
    reason_rows = [
        [label, number(mwh), number(100 * mwh / total if total else None, 1) + " %"]
        for label, mwh in reasons
    ]
    all_tables = tables(data)
    rankings = {
        "Empresas que mais perdem": all_tables["operators"],
        "Pontos de conexão que mais perdem": all_tables["connection-points"],
    }

    h2 = f'<h2 style="{_STYLES["h2"]}">'
    html = [
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 16px 0;"><tr>'
        + "".join(_kpi_html(label, value) for label, value in kpis[:2])
        + "</tr><tr>"
        + "".join(_kpi_html(label, value) for label, value in kpis[2:])
        + "</tr></table>",
        f"{h2}Motivos do corte</h2>",
        _rows_html(["Motivo", "Energia (MWh)", "Parcela"], reason_rows),
    ]
    text = [
        f"Período: {data.period_label} · {data.scope}",
        "\n".join(f"- {label}: {value}" for label, value in kpis),
        "Motivos do corte:\n" + "\n".join(f"- {r[0]}: {r[1]} MWh ({r[2]})" for r in reason_rows),
    ]
    for title, table in rankings.items():
        top = table.rows[:TOP_ROWS]
        if not top:
            continue
        rows = [[str(r[0]), str(r[1]), number(r[6]), number(r[7], 1) + " %"] for r in top]
        html += [
            f"{h2}{escape(title)}</h2>",
            _rows_html(["#", table.headers[1], "Cortada (MWh)", "Corte"], rows),
        ]
        text.append(f"{title}:\n" + "\n".join(f"{r[0]}. {r[1]}: {r[2]} MWh ({r[3]})" for r in rows))
    text.append(f"Análise completa, com gráficos e downloads: {url}")
    return "".join(html), "\n\n".join(text)
