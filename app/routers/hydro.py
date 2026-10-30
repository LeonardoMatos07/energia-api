"""Water and energy: stored energy (EAR), natural inflows (ENA), reservoirs and spilled energy."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import (
    PlantMonthlySpilledEnergy,
    Reservoir,
    ReservoirDaily,
    SubsystemStorageDaily,
)
from app.schemas import (
    MONTH_PATTERN,
    ReservoirDailyPoint,
    ReservoirDetailResponse,
    ReservoirInfo,
    ReservoirListResponse,
    ReservoirSummary,
    SpilledEnergyPoint,
    SpilledEnergyResponse,
    Subsystem,
    SubsystemStoragePoint,
    SubsystemStorageResponse,
    parse_month,
)

router = APIRouter(prefix="/v1/hydro", tags=["hydro"])

DEFAULT_DAYS = 90
MAX_DAYS = 366


def _days(
    session: SessionDep, latest: date | None, first: date | None, last: date | None
) -> tuple[date, date]:
    end = last or latest or datetime.now(UTC).date()
    start = first or end - timedelta(days=DEFAULT_DAYS - 1)
    if end < start:
        raise HTTPException(status_code=422, detail="'to' must not be before 'from'.")
    if (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_DAYS} days per request.")
    return start, end


@router.get("/subsystems", response_model=SubsystemStorageResponse)
def hydro_subsystems(
    session: SessionDep,
    cache: PublicCacheDep,
    subsystem: Subsystem | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> SubsystemStorageResponse:
    """Daily stored energy (EAR) and natural inflow energy (ENA) per subsystem."""
    latest = session.scalar(select(func.max(SubsystemStorageDaily.day)))
    first, last = _days(session, latest, date_from, date_to)

    def load() -> SubsystemStorageResponse:
        stmt = select(SubsystemStorageDaily).where(
            SubsystemStorageDaily.day >= first, SubsystemStorageDaily.day <= last
        )
        if subsystem is not None:
            stmt = stmt.where(SubsystemStorageDaily.subsystem == subsystem)
        rows = session.scalars(
            stmt.order_by(SubsystemStorageDaily.day, SubsystemStorageDaily.subsystem)
        ).all()
        return SubsystemStorageResponse(
            date_from=first,
            date_to=last,
            points=[SubsystemStoragePoint.model_validate(row) for row in rows],
        )

    return cache.get_or_set(load)


@router.get("/reservoirs", response_model=ReservoirListResponse)
def hydro_reservoirs(
    session: SessionDep,
    cache: PublicCacheDep,
    subsystem: Subsystem | None = None,
    basin: Annotated[str | None, Query(max_length=80)] = None,
) -> ReservoirListResponse:
    """Reservoir register with the level of the latest day that has data."""

    def load() -> ReservoirListResponse:
        latest = session.scalar(select(func.max(ReservoirDaily.day)))
        stmt = select(Reservoir, ReservoirDaily).outerjoin(
            ReservoirDaily,
            (ReservoirDaily.reservoir_id == Reservoir.id) & (ReservoirDaily.day == latest),
        )
        if subsystem is not None:
            stmt = stmt.where(Reservoir.subsystem == subsystem)
        if basin:
            stmt = stmt.where(func.upper(Reservoir.basin) == basin.upper())
        items = []
        for reservoir, daily in session.execute(stmt.order_by(Reservoir.name)).all():
            items.append(
                ReservoirSummary(
                    **ReservoirInfo.model_validate(reservoir).model_dump(),
                    latest_day=daily.day if daily else None,
                    storage_pct=None
                    if daily is None or daily.storage_pct is None
                    else float(daily.storage_pct),
                    ena_storable_pct_mlt=None
                    if daily is None or daily.ena_storable_pct_mlt is None
                    else float(daily.ena_storable_pct_mlt),
                )
            )
        return ReservoirListResponse(latest_day=latest, items=items)

    return cache.get_or_set(load)


@router.get(
    "/reservoirs/{slug}",
    response_model=ReservoirDetailResponse,
    responses={404: {"description": "Unknown reservoir"}},
)
def hydro_reservoir(
    slug: str,
    session: SessionDep,
    cache: PublicCacheDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> ReservoirDetailResponse:
    latest = session.scalar(select(func.max(ReservoirDaily.day)))
    first, last = _days(session, latest, date_from, date_to)

    def load() -> ReservoirDetailResponse | None:
        reservoir = session.scalars(select(Reservoir).where(Reservoir.slug == slug)).first()
        if reservoir is None:
            return None
        rows = session.scalars(
            select(ReservoirDaily)
            .where(
                ReservoirDaily.reservoir_id == reservoir.id,
                ReservoirDaily.day >= first,
                ReservoirDaily.day <= last,
            )
            .order_by(ReservoirDaily.day)
        ).all()
        return ReservoirDetailResponse(
            date_from=first,
            date_to=last,
            reservoir=ReservoirInfo.model_validate(reservoir),
            points=[ReservoirDailyPoint.model_validate(row) for row in rows],
        )

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown reservoir.")
    return result


@router.get("/spilled-energy", response_model=SpilledEnergyResponse)
def hydro_spilled_energy(
    session: SessionDep,
    cache: PublicCacheDep,
    subsystem: Subsystem | None = None,
    plant: Annotated[str | None, Query(max_length=40, description="plant_code")] = None,
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SpilledEnergyResponse:
    """Spilled energy that could have been turbined, per plant for one month (default: latest),
    largest first."""
    wanted = parse_month(month) if month else None

    def load() -> SpilledEnergyResponse:
        selected = wanted or session.scalar(select(func.max(PlantMonthlySpilledEnergy.month)))
        if selected is None:
            return SpilledEnergyResponse(points=[])
        stmt = select(PlantMonthlySpilledEnergy).where(PlantMonthlySpilledEnergy.month == selected)
        if subsystem is not None:
            stmt = stmt.where(PlantMonthlySpilledEnergy.subsystem == subsystem)
        if plant:
            stmt = stmt.where(PlantMonthlySpilledEnergy.plant_code == plant)
        rows = session.scalars(
            stmt.order_by(PlantMonthlySpilledEnergy.spilled_turbinable_energy_mwh.desc()).limit(
                limit
            )
        ).all()
        return SpilledEnergyResponse(points=[SpilledEnergyPoint.model_validate(r) for r in rows])

    return cache.get_or_set(load)
