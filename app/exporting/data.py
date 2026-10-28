"""The analyses as tables, in one place, so the PDF, the spreadsheet and the CSV cannot disagree."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SubsystemMonthlyCurtailment
from app.queries import curtailment_summary, grouped_ranking, hourly_profile
from app.schemas import (
    AggregateSource,
    CurtailmentSummary,
    HourlyProfileResponse,
    RankedGroup,
    SubsystemCurtailment,
)

RANKING_ROWS = 200
SOURCE_LABELS = {"total": "Eólica e solar", "wind": "Eólica", "solar": "Solar"}
DAY_TYPES = {"weekday": "Dia útil", "weekend": "Fim de semana"}

Cell = str | int | float | None


@dataclass(frozen=True)
class Table:
    key: str
    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    note: str | None = None


@dataclass(frozen=True)
class Analysis:
    scope: str
    source: AggregateSource
    start: date | None
    end: date | None
    summary: CurtailmentSummary
    subsystems: list[SubsystemCurtailment]
    operators: list[RankedGroup]
    connection_points: list[RankedGroup]
    hourly: HourlyProfileResponse

    @property
    def period_label(self) -> str:
        first = self.start or self.summary.start
        last = self.end or self.summary.end
        if first is None or last is None:
            return "todo o período"
        if first == last:
            return f"{first:%m/%Y}"
        return f"{first:%m/%Y} a {last:%m/%Y}"

    @property
    def file_stem(self) -> str:
        first = self.start or self.summary.start
        last = self.end or self.summary.end
        period = f"{first:%Y-%m}_{last:%Y-%m}" if first and last else "completo"
        return f"spillway-analise-{self.scope.lower()}-{self.source}-{period}"


def load_analysis(
    session: Session,
    scope: str,
    source: AggregateSource,
    start: date | None,
    end: date | None,
) -> Analysis:
    stmt = (
        select(SubsystemMonthlyCurtailment)
        .where(SubsystemMonthlyCurtailment.source == source)
        .order_by(SubsystemMonthlyCurtailment.month, SubsystemMonthlyCurtailment.subsystem)
    )
    if start is not None:
        stmt = stmt.where(SubsystemMonthlyCurtailment.month >= start)
    if end is not None:
        stmt = stmt.where(SubsystemMonthlyCurtailment.month <= end)
    if scope != "SIN":
        stmt = stmt.where(SubsystemMonthlyCurtailment.subsystem == scope)
    return Analysis(
        scope=scope,
        source=source,
        start=start,
        end=end,
        summary=curtailment_summary(session, scope, source, start, end),
        subsystems=[SubsystemCurtailment.model_validate(r) for r in session.scalars(stmt)],
        operators=grouped_ranking(session, "operator", source, start, end, RANKING_ROWS),
        connection_points=grouped_ranking(
            session, "connection_point", source, start, end, RANKING_ROWS
        ),
        hourly=hourly_profile(session, scope, source, start, end),
    )


def _ranking_table(key: str, title: str, label: str, rows: list[RankedGroup]) -> Table:
    return Table(
        key=key,
        title=title,
        headers=(
            "Posição",
            label,
            "UF",
            "Usinas",
            "Usinas afetadas",
            "Geração verificada (MWh)",
            "Energia cortada (MWh)",
            "Corte (%)",
            "Perda estimada (R$)",
            "Compensável (MWh)",
        ),
        rows=tuple(
            (
                r.rank,
                r.name,
                r.state,
                r.plants,
                r.affected_plants,
                r.verified_generation_mwh,
                r.curtailed_mwh,
                r.curtailment_pct,
                r.estimated_loss_brl,
                r.compensable_mwh,
            )
            for r in rows
        ),
    )


def tables(data: Analysis) -> dict[str, Table]:
    s = data.summary
    summary = Table(
        key="summary",
        title="Resumo",
        headers=("Indicador", "Valor"),
        rows=(
            ("Escopo", data.scope),
            ("Fonte", SOURCE_LABELS[data.source]),
            ("Período", data.period_label),
            ("Meses", s.months),
            ("Geração verificada (MWh)", s.verified_generation_mwh),
            ("Energia cortada (MWh)", s.curtailed_mwh),
            ("Corte (%)", s.curtailment_pct),
            ("Confiabilidade elétrica, REL (MWh)", s.curtailed_rel_mwh),
            ("Indisponibilidade externa, CNF (MWh)", s.curtailed_cnf_mwh),
            ("Razão energética, ENE (MWh)", s.curtailed_ene_mwh),
            ("Outros motivos (MWh)", s.curtailed_other_mwh),
            ("Perda estimada (R$)", s.estimated_loss_brl),
            ("Usinas afetadas", s.affected_plants),
        ),
    )
    subsystems = Table(
        key="subsystems",
        title="Subsistemas por mês",
        headers=(
            "Mês",
            "Subsistema",
            "Geração verificada (MWh)",
            "Energia cortada (MWh)",
            "Corte (%)",
            "Confiabilidade elétrica, REL (MWh)",
            "Indisponibilidade externa, CNF (MWh)",
            "Razão energética, ENE (MWh)",
            "Outros motivos (MWh)",
            "Perda estimada (R$)",
            "Usinas afetadas",
        ),
        rows=tuple(
            (
                f"{r.month:%Y-%m}",
                r.subsystem,
                r.verified_generation_mwh,
                r.curtailed_mwh,
                r.curtailment_pct,
                r.curtailed_rel_mwh,
                r.curtailed_cnf_mwh,
                r.curtailed_ene_mwh,
                r.curtailed_other_mwh,
                r.estimated_loss_brl,
                r.affected_plants,
            )
            for r in data.subsystems
        ),
    )
    hourly = Table(
        key="hourly",
        title="Perfil horário",
        headers=(
            "Tipo de dia",
            "Hora",
            "Corte médio (MWh)",
            "Geração média (MWh)",
            "Corte (%)",
        ),
        rows=tuple(
            (
                DAY_TYPES[p.day_type],
                p.hour,
                p.avg_curtailed_mwh,
                p.avg_generation_mwh,
                p.curtailment_pct,
            )
            for p in data.hourly.points
        ),
        note=f"Média por hora ao longo de {data.hourly.months} meses.",
    )
    return {
        "summary": summary,
        "subsystems": subsystems,
        "operators": _ranking_table("operators", "Empresas", "Empresa", data.operators),
        "connection-points": _ranking_table(
            "connection-points", "Pontos de conexão", "Ponto de conexão", data.connection_points
        ),
        "hourly": hourly,
    }
