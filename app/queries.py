from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plant, PlantMonthlyCurtailment
from app.schemas import AggregateSource, PlantSummary, RankedPlant


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
