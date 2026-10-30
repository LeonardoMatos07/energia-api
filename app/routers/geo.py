"""Map layers: values per place, without geometry (the web draws the outlines itself)."""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import StateMonthlyCurtailment
from app.routers.curtailment import STATES, MonthQuery
from app.schemas import (
    AggregateSource,
    GeoStateCurtailment,
    GeoStatesMeta,
    GeoStatesResponse,
    parse_month,
)

router = APIRouter(prefix="/v1/geo", tags=["geo"])

STATES_WARNINGS = (
    "Valores por unidade da federação, somados a partir das usinas eólicas e solares despachadas "
    "pelo ONS; estados sem essas usinas não aparecem.",
    "A porcentagem é a energia cortada sobre a energia que a usina teria gerado (verificada mais "
    "cortada).",
)


@router.get("/states/curtailment", response_model=GeoStatesResponse)
def states_curtailment(
    session: SessionDep,
    cache: PublicCacheDep,
    source: Annotated[AggregateSource, Query()] = "total",
    month: MonthQuery = None,
) -> GeoStatesResponse:
    """Curtailment of one month per state. Same data as `/v1/curtailment/states/{state}`."""

    def load() -> GeoStatesResponse:
        months = [
            m
            for m in session.scalars(
                select(StateMonthlyCurtailment.month)
                .where(StateMonthlyCurtailment.source == source)
                .distinct()
                .order_by(StateMonthlyCurtailment.month.desc())
            )
        ]
        if not months:
            return _response(source, None, [], [], None)
        chosen = parse_month(month) if month is not None else months[0]
        if chosen not in months:
            raise HTTPException(status_code=404, detail="No data for this month.")
        rows = session.scalars(
            select(StateMonthlyCurtailment)
            .where(
                StateMonthlyCurtailment.source == source,
                StateMonthlyCurtailment.month == chosen,
                StateMonthlyCurtailment.state.in_(STATES),
            )
            .order_by(StateMonthlyCurtailment.state)
        ).all()
        updated = session.scalar(
            select(func.max(StateMonthlyCurtailment.updated_at)).where(
                StateMonthlyCurtailment.source == source,
                StateMonthlyCurtailment.month == chosen,
            )
        )
        items = [
            GeoStateCurtailment(
                state=r.state,
                curtailment_pct=None if r.curtailment_pct is None else float(r.curtailment_pct),
                curtailed_mwh=float(r.curtailed_mwh),
                verified_generation_mwh=float(r.verified_generation_mwh),
                estimated_loss_brl=(
                    None if r.estimated_loss_brl is None else float(r.estimated_loss_brl)
                ),
                affected_plants=r.affected_plants,
            )
            for r in rows
        ]
        return _response(source, chosen, items, months, updated)

    return cache.get_or_set(load)


def _response(
    source: AggregateSource,
    month: date | None,
    items: list[GeoStateCurtailment],
    months: list[date],
    updated: datetime | None,
) -> GeoStatesResponse:
    present = {i.state for i in items}
    return GeoStatesResponse(
        meta=GeoStatesMeta(
            source_name="Operador Nacional do Sistema Elétrico (ONS), dados abertos",
            source_url="https://dados.ons.org.br/",
            license="Termos de uso de cada conjunto em dados.ons.org.br; processado pelo Spillway.",
            data_source=source,
            month=month,
            months=sorted(months),
            updated_at=(updated if updated is None else updated.astimezone(UTC)),
            warnings=list(STATES_WARNINGS),
            omitted=len(STATES) - len(present) if month is not None else len(STATES),
            omitted_states=sorted(STATES - present),
        ),
        items=items,
    )
