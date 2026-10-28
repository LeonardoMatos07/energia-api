"""The analyses as tables, in one place, so the PDF, the spreadsheet and the CSV cannot disagree."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Plant,
    PlantMonthlyCurtailment,
    StateMonthlyCurtailment,
    SubsystemMonthlyCurtailment,
)
from app.queries import curtailment_summary, grouped_ranking, hourly_profile
from app.schemas import (
    AggregateSource,
    CurtailmentSummary,
    HourlyProfileResponse,
    RankedGroup,
    StateCurtailment,
    SubsystemCurtailment,
)

EVERY_ROW = 1_000_000
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
class PlantMonth:
    month: date
    name: str
    source: str
    state: str
    subsystem: str
    operator: str | None
    connection_point: str | None
    capacity_mw: float | None
    generation_mwh: float
    curtailed_mwh: float
    curtailment_pct: float | None
    rel_mwh: float
    cnf_mwh: float
    ene_mwh: float
    other_mwh: float
    loss_brl: float | None
    restricted_hours: float


@dataclass(frozen=True)
class Analysis:
    scope: str
    source: AggregateSource
    start: date | None
    end: date | None
    summary: CurtailmentSummary
    subsystems: list[SubsystemCurtailment]
    states: list[StateCurtailment]
    plants: list[PlantMonth]
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
        states=_states(session, scope, source, start, end),
        plants=_plants(session, scope, source, start, end),
        operators=grouped_ranking(session, "operator", source, start, end, EVERY_ROW),
        connection_points=grouped_ranking(
            session, "connection_point", source, start, end, EVERY_ROW
        ),
        hourly=hourly_profile(session, scope, source, start, end),
    )


def _states(
    session: Session, scope: str, source: AggregateSource, start: date | None, end: date | None
) -> list[StateCurtailment]:
    stmt = (
        select(StateMonthlyCurtailment)
        .where(StateMonthlyCurtailment.source == source)
        .order_by(StateMonthlyCurtailment.month, StateMonthlyCurtailment.state)
    )
    if start is not None:
        stmt = stmt.where(StateMonthlyCurtailment.month >= start)
    if end is not None:
        stmt = stmt.where(StateMonthlyCurtailment.month <= end)
    if scope != "SIN":
        in_scope = select(Plant.state).where(Plant.subsystem == scope).distinct()
        stmt = stmt.where(StateMonthlyCurtailment.state.in_(in_scope))
    return [StateCurtailment.model_validate(r) for r in session.scalars(stmt)]


def _plants(
    session: Session, scope: str, source: AggregateSource, start: date | None, end: date | None
) -> list[PlantMonth]:
    metrics = PlantMonthlyCurtailment
    stmt = (
        select(Plant, metrics)
        .join(metrics, metrics.plant_id == Plant.id)
        .where(Plant.kind == "plant")
        .order_by(metrics.month, Plant.state, Plant.name)
    )
    if source != "total":
        stmt = stmt.where(Plant.source == source)
    if scope != "SIN":
        stmt = stmt.where(Plant.subsystem == scope)
    if start is not None:
        stmt = stmt.where(metrics.month >= start)
    if end is not None:
        stmt = stmt.where(metrics.month <= end)

    def number(value: object) -> float | None:
        return None if value is None else float(value)  # type: ignore[arg-type]

    return [
        PlantMonth(
            month=m.month,
            name=p.name,
            source=p.source,
            state=p.state,
            subsystem=p.subsystem,
            operator=p.operator,
            connection_point=p.connection_point,
            capacity_mw=number(p.capacity_mw),
            generation_mwh=float(m.verified_generation_mwh),
            curtailed_mwh=float(m.curtailed_mwh),
            curtailment_pct=number(m.curtailment_pct),
            rel_mwh=float(m.curtailed_rel_mwh),
            cnf_mwh=float(m.curtailed_cnf_mwh),
            ene_mwh=float(m.curtailed_ene_mwh),
            other_mwh=float(m.curtailed_other_mwh),
            loss_brl=number(m.estimated_loss_brl),
            restricted_hours=float(m.restricted_hours),
        )
        for p, m in session.execute(stmt)
    ]


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
    states = Table(
        key="states",
        title="Estados por mês",
        headers=(
            "Mês",
            "UF",
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
                r.state,
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
            for r in data.states
        ),
    )
    plants = Table(
        key="plants",
        title="Usinas por mês",
        headers=(
            "Mês",
            "Usina",
            "Fonte",
            "UF",
            "Subsistema",
            "Empresa",
            "Ponto de conexão",
            "Capacidade (MW)",
            "Geração verificada (MWh)",
            "Energia cortada (MWh)",
            "Corte (%)",
            "Confiabilidade elétrica, REL (MWh)",
            "Indisponibilidade externa, CNF (MWh)",
            "Razão energética, ENE (MWh)",
            "Outros motivos (MWh)",
            "Perda estimada (R$)",
            "Horas com restrição",
        ),
        rows=tuple(
            (
                f"{r.month:%Y-%m}",
                r.name,
                SOURCE_LABELS.get(r.source, r.source),
                r.state,
                r.subsystem,
                r.operator,
                r.connection_point,
                r.capacity_mw,
                r.generation_mwh,
                r.curtailed_mwh,
                r.curtailment_pct,
                r.rel_mwh,
                r.cnf_mwh,
                r.ene_mwh,
                r.other_mwh,
                r.loss_brl,
                r.restricted_hours,
            )
            for r in data.plants
        ),
    )
    return {
        "summary": summary,
        "subsystems": subsystems,
        "states": states,
        "operators": _ranking_table("operators", "Empresas", "Empresa", data.operators),
        "connection-points": _ranking_table(
            "connection-points", "Pontos de conexão", "Ponto de conexão", data.connection_points
        ),
        "hourly": hourly,
        "plants": plants,
    }
