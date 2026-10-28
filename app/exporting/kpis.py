"""The month's headline numbers as tiles for the report e-mail, with how they moved."""

from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Literal

from sqlalchemy.orm import Session

from app.exporting.charts import format_number
from app.queries import curtailment_summary
from app.schemas import CurtailmentSummary

Tone = Literal["good", "bad", "neutral"]
TONE_COLORS = {"good": "#067647", "bad": "#b42318", "neutral": "#52667a"}
MINUS = "\N{MINUS SIGN}"


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    notes: tuple[tuple[str, Tone], ...] = ()


def shift_month(month: date, months: int) -> date:
    index = month.year * 12 + (month.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _signed(value: float, digits: int, suffix: str) -> str:
    sign = "+" if value > 0 else MINUS if value < 0 else ""
    return f"{sign}{format_number(abs(value), digits)}{suffix}"


def _change(now: float | None, before: float | None) -> float | None:
    if now is None or before is None or before == 0:
        return None
    return (now / before - 1) * 100


def _tone(delta: float) -> Tone:
    # More energy cut is worse; less is better.
    return "neutral" if abs(delta) < 0.05 else ("bad" if delta > 0 else "good")


def _percent_note(now: float | None, before: float | None, label: str) -> tuple[str, Tone] | None:
    delta = _change(now, before)
    return None if delta is None else (f"{_signed(delta, 1, '%')} {label}", _tone(delta))


def _points_note(now: float | None, before: float | None, label: str) -> tuple[str, Tone] | None:
    if now is None or before is None:
        return None
    delta = now - before
    return f"{_signed(delta, 1, ' p.p.')} {label}", _tone(delta)


def _read(summary: CurtailmentSummary | None, field: str) -> float | None:
    value = None if summary is None else getattr(summary, field)
    return None if value is None else float(value)


def _notes(*notes: tuple[str, Tone] | None) -> tuple[tuple[str, Tone], ...]:
    return tuple(note for note in notes if note is not None)


def _million(value: float | None) -> str:
    if value is None:
        return "—"
    return f"R$ {format_number(value / 1_000_000, 1)} milhões"


def month_kpis(session: Session, month: date) -> list[Kpi]:
    """Four tiles for a month of the national total (wind and solar), compared with the month
    before and the same month a year earlier. Empty when the month has no data."""

    def national(target: date) -> CurtailmentSummary | None:
        found = curtailment_summary(session, "SIN", "total", target, target)
        return found if found.months else None

    now = national(month)
    if now is None:
        return []
    previous = national(shift_month(month, -1))
    year_ago = national(shift_month(month, -12))
    vs_month, vs_year = "vs. mês anterior", "vs. mesmo mês do ano anterior"

    return [
        Kpi(
            "Energia cortada",
            f"{format_number(now.curtailed_mwh / 1000)} GWh",
            _notes(
                _percent_note(now.curtailed_mwh, _read(previous, "curtailed_mwh"), vs_month),
                _percent_note(now.curtailed_mwh, _read(year_ago, "curtailed_mwh"), vs_year),
            ),
        ),
        Kpi(
            "% de corte",
            f"{format_number(now.curtailment_pct or 0)} %",
            _notes(_points_note(now.curtailment_pct, _read(previous, "curtailment_pct"), vs_month)),
        ),
        Kpi(
            "Perda estimada",
            _million(now.estimated_loss_brl),
            _notes(
                _percent_note(
                    now.estimated_loss_brl, _read(previous, "estimated_loss_brl"), vs_month
                )
            ),
        ),
        Kpi(
            "Usinas afetadas",
            format_number(now.affected_plants, 0),
            _notes(
                _percent_note(now.affected_plants, _read(previous, "affected_plants"), vs_month)
            ),
        ),
    ]


def _tile(kpi: Kpi) -> str:
    notes = "".join(
        f'<div style="font-size:12px;line-height:17px;color:{TONE_COLORS[tone]};">'
        f"{escape(text)}</div>"
        for text, tone in kpi.notes
    )
    return (
        '<td width="50%" valign="top" style="padding:4px;">'
        '<div style="background:#e9f1ef;border-radius:10px;padding:14px 14px 12px 14px;">'
        f'<div style="font-size:12px;line-height:16px;color:#52667a;">{escape(kpi.label)}</div>'
        '<div style="font-size:22px;line-height:28px;font-weight:700;color:#0e1b2c;'
        f'margin:2px 0 6px 0;">{escape(kpi.value)}</div>{notes}</div></td>'
    )


def kpis_html(kpis: list[Kpi]) -> str:
    """Two by two, as a table, so it holds up in every mail client."""
    rows = "".join(
        "<tr>" + "".join(_tile(k) for k in kpis[i : i + 2]) + "</tr>"
        for i in range(0, len(kpis), 2)
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 18px 0;">{rows}</table>'
    )


def kpis_text(kpis: list[Kpi]) -> str:
    lines = ["O mês em números:"]
    for kpi in kpis:
        notes = "; ".join(text for text, _ in kpi.notes)
        lines.append(f"- {kpi.label}: {kpi.value}" + (f" ({notes})" if notes else ""))
    return "\n".join(lines)
