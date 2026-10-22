from datetime import date

from sqlalchemy import Column, func, select
from sqlalchemy.orm import Session

from app.models import (
    HourlyCurtailment,
    Plant,
    PlantMonthlyCurtailment,
    StateMonthlyCurtailment,
    SubsystemMonthlyCurtailment,
)
from app.schemas import (
    AggregateSource,
    CurtailmentSummary,
    HourlyPoint,
    HourlyProfileResponse,
    PlantSummary,
    RankedGroup,
    RankedPlant,
)

SUBSYSTEM_SCOPES = frozenset({"N", "NE", "S", "SE", "SIN"})


def latest_plant_month(session: Session, state: str | None = None) -> date | None:
    stmt = (
        select(PlantMonthlyCurtailment.month)
        .order_by(PlantMonthlyCurtailment.month.desc())
        .limit(1)
    )
    if state is not None:
        stmt = stmt.join(Plant, Plant.id == PlantMonthlyCurtailment.plant_id).where(
            Plant.state == state
        )
    return session.scalars(stmt).first()


def plant_ranking(
    session: Session, month: date, state: str | None, source: AggregateSource, limit: int
) -> list[RankedPlant]:
    stmt = (
        select(Plant, PlantMonthlyCurtailment)
        .join(PlantMonthlyCurtailment, PlantMonthlyCurtailment.plant_id == Plant.id)
        .where(PlantMonthlyCurtailment.month == month, Plant.kind == "plant")
        .order_by(PlantMonthlyCurtailment.curtailed_mwh.desc(), Plant.id)
        .limit(limit)
    )
    if state is not None:
        stmt = stmt.where(Plant.state == state)
    if source != "total":
        stmt = stmt.where(Plant.source == source)

    items: list[RankedPlant] = []
    for index, (plant, row) in enumerate(session.execute(stmt), start=1):
        stored = None
        if source != "total":
            stored = row.state_rank if state is not None else row.national_rank
        summary = PlantSummary.model_validate(plant).model_dump()
        items.append(
            RankedPlant.model_validate(
                {
                    **summary,
                    "rank": stored if stored is not None else index,
                    "curtailed_mwh": row.curtailed_mwh,
                    "curtailment_pct": row.curtailment_pct,
                    "estimated_loss_brl": row.estimated_loss_brl,
                }
            )
        )
    return items


def curtailment_summary(
    session: Session,
    scope: str,
    source: AggregateSource,
    start: date | None,
    end: date | None,
) -> CurtailmentSummary:
    if scope in SUBSYSTEM_SCOPES:
        table: type[SubsystemMonthlyCurtailment] | type[StateMonthlyCurtailment] = (
            SubsystemMonthlyCurtailment
        )
        scope_filter = table.subsystem == scope  # type: ignore[union-attr]
    else:
        table = StateMonthlyCurtailment
        scope_filter = table.state == scope

    totals = select(
        func.count(func.distinct(table.month)),
        func.coalesce(func.sum(table.verified_generation_mwh), 0),
        func.coalesce(func.sum(table.curtailed_mwh), 0),
        func.sum(table.estimated_loss_brl),
        func.coalesce(func.sum(table.curtailed_rel_mwh), 0),
        func.coalesce(func.sum(table.curtailed_cnf_mwh), 0),
        func.coalesce(func.sum(table.curtailed_ene_mwh), 0),
        func.coalesce(func.sum(table.curtailed_other_mwh), 0),
    ).where(scope_filter, table.source == source)
    plants = (
        select(func.count(func.distinct(PlantMonthlyCurtailment.plant_id)))
        .join(Plant, Plant.id == PlantMonthlyCurtailment.plant_id)
        .where(Plant.kind == "plant", PlantMonthlyCurtailment.curtailed_mwh > 0)
    )
    if scope in SUBSYSTEM_SCOPES - {"SIN"}:
        plants = plants.where(Plant.subsystem == scope)
    elif scope not in SUBSYSTEM_SCOPES:
        plants = plants.where(Plant.state == scope)
    if source != "total":
        plants = plants.where(Plant.source == source)
    if start is not None:
        totals = totals.where(table.month >= start)
        plants = plants.where(PlantMonthlyCurtailment.month >= start)
    if end is not None:
        totals = totals.where(table.month <= end)
        plants = plants.where(PlantMonthlyCurtailment.month <= end)

    months, verified, curtailed, loss, rel, cnf, ene, other = session.execute(totals).one()
    potential = float(verified) + float(curtailed)
    return CurtailmentSummary(
        scope=scope,
        source=source,
        start=start,
        end=end,
        months=int(months),
        verified_generation_mwh=float(verified),
        curtailed_mwh=float(curtailed),
        curtailed_rel_mwh=float(rel),
        curtailed_cnf_mwh=float(cnf),
        curtailed_ene_mwh=float(ene),
        curtailed_other_mwh=float(other),
        curtailment_pct=round(float(curtailed) / potential * 100, 2) if potential > 0 else None,
        estimated_loss_brl=None if loss is None else float(loss),
        affected_plants=int(session.scalar(plants) or 0),
    )


RANKING_COLUMNS: dict[str, Column[str]] = {
    "operator": Plant.operator,  # type: ignore[dict-item]
    "connection_point": Plant.connection_point,  # type: ignore[dict-item]
}


def grouped_ranking(
    session: Session,
    group: str,
    source: AggregateSource,
    start: date | None,
    end: date | None,
    limit: int,
) -> list[RankedGroup]:
    column = RANKING_COLUMNS[group]
    metrics = PlantMonthlyCurtailment
    stmt = (
        select(
            column,
            func.min(Plant.state),
            func.count(func.distinct(Plant.id)),
            func.count(func.distinct(Plant.id)).filter(metrics.curtailed_mwh > 0),
            func.sum(metrics.verified_generation_mwh),
            func.sum(metrics.curtailed_mwh),
            func.sum(metrics.estimated_loss_brl),
            func.sum(metrics.curtailed_rel_mwh + metrics.curtailed_cnf_mwh),
        )
        .join(metrics, metrics.plant_id == Plant.id)
        .where(Plant.kind == "plant", column.is_not(None))
        .group_by(column)
        .order_by(func.sum(metrics.curtailed_mwh).desc(), column)
        .limit(limit)
    )
    if source != "total":
        stmt = stmt.where(Plant.source == source)
    if start is not None:
        stmt = stmt.where(metrics.month >= start)
    if end is not None:
        stmt = stmt.where(metrics.month <= end)

    items = []
    for rank, row in enumerate(session.execute(stmt), start=1):
        name, state, plants, affected, verified, curtailed, loss, compensable = row
        potential = float(verified or 0) + float(curtailed or 0)
        items.append(
            RankedGroup(
                rank=rank,
                name=name,
                state=state if group == "connection_point" else None,
                plants=int(plants),
                affected_plants=int(affected),
                verified_generation_mwh=float(verified or 0),
                curtailed_mwh=float(curtailed or 0),
                curtailment_pct=(
                    round(float(curtailed or 0) / potential * 100, 2) if potential > 0 else None
                ),
                estimated_loss_brl=None if loss is None else float(loss),
                compensable_mwh=float(compensable or 0),
            )
        )
    return items


def hourly_profile(
    session: Session,
    scope: str,
    source: AggregateSource,
    start: date | None,
    end: date | None,
) -> HourlyProfileResponse:
    table = HourlyCurtailment
    stmt = (
        select(
            table.day_type,
            table.hour,
            func.sum(table.curtailed_mwh),
            func.sum(table.generation_mwh),
            func.sum(table.days),
        )
        .where(table.subsystem == scope, table.source == source)
        .group_by(table.day_type, table.hour)
        .order_by(table.day_type, table.hour)
    )
    months = select(func.count(func.distinct(table.month))).where(
        table.subsystem == scope, table.source == source
    )
    if start is not None:
        stmt = stmt.where(table.month >= start)
        months = months.where(table.month >= start)
    if end is not None:
        stmt = stmt.where(table.month <= end)
        months = months.where(table.month <= end)

    points = []
    for day_type, hour, curtailed, generation, days in session.execute(stmt):
        day_count = int(days) or 1
        potential = float(curtailed) + float(generation)
        points.append(
            HourlyPoint(
                day_type=day_type,
                hour=int(hour),
                avg_curtailed_mwh=float(curtailed) / day_count,
                avg_generation_mwh=float(generation) / day_count,
                curtailment_pct=(
                    round(float(curtailed) / potential * 100, 2) if potential > 0 else None
                ),
            )
        )
    return HourlyProfileResponse(
        scope=scope,
        source=source,
        start=start,
        end=end,
        months=int(session.scalar(months) or 0),
        points=points,
    )
