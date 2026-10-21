from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Plant,
    PlantMonthlyCurtailment,
    StateMonthlyCurtailment,
    SubsystemMonthlyCurtailment,
)
from app.schemas import AggregateSource, CurtailmentSummary, PlantSummary, RankedPlant

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

    months, verified, curtailed, loss = session.execute(totals).one()
    potential = float(verified) + float(curtailed)
    return CurtailmentSummary(
        scope=scope,
        source=source,
        start=start,
        end=end,
        months=int(months),
        verified_generation_mwh=float(verified),
        curtailed_mwh=float(curtailed),
        curtailment_pct=round(float(curtailed) / potential * 100, 2) if potential > 0 else None,
        estimated_loss_brl=None if loss is None else float(loss),
        affected_plants=int(session.scalar(plants) or 0),
    )
