"""Power system data from the ONS: hourly balance, monthly generation by source and per plant."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import (
    PlantMonthlyGeneration,
    PowerPlant,
    SubsystemHourlyBalance,
    SubsystemMonthlyGeneration,
)
from app.schemas import (
    MONTH_PATTERN,
    BalancePoint,
    BalanceResponse,
    MonthlyGenerationPoint,
    MonthlyGenerationResponse,
    PlantGenerationPoint,
    PlantGenerationResponse,
    PowerPlantInfo,
    Subsystem,
    SubsystemOrSin,
    parse_month,
)

router = APIRouter(prefix="/v1", tags=["system"])

MAX_BALANCE_DAYS = 31
DEFAULT_BALANCE_DAYS = 7
DEFAULT_MONTHS = 12
MonthQuery = Annotated[str | None, Query(pattern=MONTH_PATTERN, examples=["2026-09"])]


def _months_back(month: date, count: int) -> date:
    index = month.year * 12 + month.month - 1 - count
    return date(index // 12, index % 12 + 1, 1)


def _months(first: str | None, last: str | None) -> tuple[date | None, date | None]:
    return (parse_month(first) if first else None, parse_month(last) if last else None)


@router.get(
    "/system/balance",
    response_model=BalanceResponse,
    responses={422: {"description": "Range larger than 31 days"}},
)
def system_balance(
    session: SessionDep,
    cache: PublicCacheDep,
    subsystem: Subsystem | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> BalanceResponse:
    """Hourly generation by source, load and net interchange, in average MW (Brasilia time)."""
    if date_from is None or date_to is None:
        latest = session.scalar(
            select(SubsystemHourlyBalance.hour)
            .order_by(SubsystemHourlyBalance.hour.desc())
            .limit(1)
        )
        last_day = date_to or (latest.date() if latest else datetime.now(UTC).date())
        date_to = last_day
        date_from = date_from or last_day - timedelta(days=DEFAULT_BALANCE_DAYS - 1)
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="'to' must not be before 'from'.")
    if (date_to - date_from).days + 1 > MAX_BALANCE_DAYS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_BALANCE_DAYS} days per request.")
    first, last = date_from, date_to

    def load() -> BalanceResponse:
        stmt = select(SubsystemHourlyBalance).where(
            SubsystemHourlyBalance.hour >= first,
            SubsystemHourlyBalance.hour < last + timedelta(days=1),
        )
        if subsystem is not None:
            stmt = stmt.where(SubsystemHourlyBalance.subsystem == subsystem)
        rows = session.scalars(
            stmt.order_by(SubsystemHourlyBalance.hour, SubsystemHourlyBalance.subsystem)
        ).all()
        return BalanceResponse(
            date_from=first,
            date_to=last,
            points=[BalancePoint.model_validate(row) for row in rows],
        )

    return cache.get_or_set(load)


@router.get("/system/generation", response_model=MonthlyGenerationResponse)
def system_generation(
    session: SessionDep,
    cache: PublicCacheDep,
    subsystem: SubsystemOrSin = "SIN",
    month_from: Annotated[str | None, Query(alias="from", pattern=MONTH_PATTERN)] = None,
    month_to: Annotated[str | None, Query(alias="to", pattern=MONTH_PATTERN)] = None,
) -> MonthlyGenerationResponse:
    """Monthly energy, average and peak power and share by source (default: whole system)."""
    first, last = _months(month_from, month_to)

    def load() -> MonthlyGenerationResponse:
        end = last or session.scalar(
            select(SubsystemMonthlyGeneration.month)
            .order_by(SubsystemMonthlyGeneration.month.desc())
            .limit(1)
        )
        if end is None:
            return MonthlyGenerationResponse(points=[])
        start = first or _months_back(end, DEFAULT_MONTHS - 1)
        rows = session.scalars(
            select(SubsystemMonthlyGeneration)
            .where(
                SubsystemMonthlyGeneration.subsystem == subsystem,
                SubsystemMonthlyGeneration.month >= start,
                SubsystemMonthlyGeneration.month <= end,
            )
            .order_by(SubsystemMonthlyGeneration.month, SubsystemMonthlyGeneration.source)
        ).all()
        return MonthlyGenerationResponse(
            points=[MonthlyGenerationPoint.model_validate(row) for row in rows]
        )

    return cache.get_or_set(load)


@router.get(
    "/plants/{slug}/generation",
    response_model=PlantGenerationResponse,
    responses={404: {"description": "Unknown plant"}},
)
def plant_generation(
    slug: str,
    session: SessionDep,
    cache: PublicCacheDep,
    month_from: Annotated[str | None, Query(alias="from", pattern=MONTH_PATTERN)] = None,
    month_to: Annotated[str | None, Query(alias="to", pattern=MONTH_PATTERN)] = None,
) -> PlantGenerationResponse:
    first, last = _months(month_from, month_to)

    def load() -> PlantGenerationResponse | None:
        plant = session.scalars(select(PowerPlant).where(PowerPlant.slug == slug)).first()
        if plant is None:
            return None
        stmt = select(PlantMonthlyGeneration).where(PlantMonthlyGeneration.plant_id == plant.id)
        if first is not None:
            stmt = stmt.where(PlantMonthlyGeneration.month >= first)
        if last is not None:
            stmt = stmt.where(PlantMonthlyGeneration.month <= last)
        rows = session.scalars(stmt.order_by(PlantMonthlyGeneration.month)).all()
        return PlantGenerationResponse(
            plant=PowerPlantInfo.model_validate(plant),
            points=[PlantGenerationPoint.model_validate(row) for row in rows],
        )

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown plant.")
    return result
