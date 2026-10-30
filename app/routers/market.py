"""Energy market prices (CCEE PLD) and quality of supply (ANEEL DEC and FEC)."""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import (
    DataSource,
    Distributor,
    PldDaily,
    PldHourly,
    PldMonthly,
    PldWeekly,
    ReliabilityMonthly,
)
from app.schemas import (
    DataSourceInfo,
    PldGranularity,
    PldPoint,
    PldResponse,
    PldVariant,
    ReliabilityDetailResponse,
    ReliabilityDistributor,
    ReliabilityMonthPoint,
    ReliabilityResponse,
    Subsystem,
)

router = APIRouter(tags=["market"])

DEFAULT_DAYS = {"hourly": 7, "daily": 90, "weekly": 365, "monthly": 730}
MAX_DAYS = {"hourly": 31, "daily": 366, "weekly": 3660, "monthly": 9000}


def _source(session: SessionDep, slug: str) -> DataSourceInfo:
    row = session.get(DataSource, slug)
    if row is None:
        raise HTTPException(status_code=503, detail=f"Data source {slug!r} is not registered.")
    return DataSourceInfo.model_validate(row)


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _window(
    granularity: PldGranularity, latest: date | None, first: date | None, last: date | None
) -> tuple[date, date]:
    end = last or latest or datetime.now(UTC).date()
    start = first or end - timedelta(days=DEFAULT_DAYS[granularity] - 1)
    if end < start:
        raise HTTPException(status_code=422, detail="'to' must not be before 'from'.")
    if (end - start).days + 1 > MAX_DAYS[granularity]:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_DAYS[granularity]} days per {granularity} request.",
        )
    return start, end


@router.get("/v1/market/pld", response_model=PldResponse)
def market_pld(
    session: SessionDep,
    cache: PublicCacheDep,
    granularity: PldGranularity = "daily",
    subsystem: Subsystem | None = None,
    variant: PldVariant = "official",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> PldResponse:
    """PLD per submarket. Hourly (max 31 days), daily, weekly average or monthly statistics.

    `variant=shadow` is the CCEE "shadow" simulation, which is not the settlement price. It only
    exists for the hourly series (2022 to 2024).
    """
    if variant == "shadow" and granularity != "hourly":
        raise HTTPException(status_code=422, detail="The shadow PLD exists only as hourly data.")

    def load() -> PldResponse:
        latest = _latest(session, granularity, variant)
        first, last = _window(granularity, latest, date_from, date_to)
        points = _points(session, granularity, variant, subsystem, first, last)
        return PldResponse(
            granularity=granularity,
            variant=variant,
            date_from=first,
            date_to=last,
            source=_source(session, "ccee-pld"),
            points=points,
        )

    return cache.get_or_set(load)


def _latest(session: SessionDep, granularity: PldGranularity, variant: PldVariant) -> date | None:
    if granularity == "hourly":
        value = session.scalar(select(func.max(PldHourly.hour)).where(PldHourly.variant == variant))
        return value.date() if value else None
    column = {"daily": PldDaily.day, "weekly": PldWeekly.week_start, "monthly": PldMonthly.month}
    return session.scalar(select(func.max(column[granularity])))


def _points(
    session: SessionDep,
    granularity: PldGranularity,
    variant: PldVariant,
    subsystem: str | None,
    first: date,
    last: date,
) -> list[PldPoint]:
    if granularity == "hourly":
        stmt = (
            select(PldHourly)
            .where(
                PldHourly.variant == variant,
                PldHourly.hour >= datetime.combine(first, time.min),
                PldHourly.hour < datetime.combine(last + timedelta(days=1), time.min),
            )
            .order_by(PldHourly.hour, PldHourly.subsystem)
        )
        if subsystem:
            stmt = stmt.where(PldHourly.subsystem == subsystem)
        return [
            PldPoint(
                period=row.hour.isoformat(),
                subsystem=row.subsystem,
                pld_avg_brl_per_mwh=float(row.pld_brl_per_mwh),
            )
            for row in session.scalars(stmt).all()
        ]
    if granularity == "weekly":
        weekly = select(PldWeekly).where(
            PldWeekly.week_start >= first, PldWeekly.week_start <= last
        )
        if subsystem:
            weekly = weekly.where(PldWeekly.subsystem == subsystem)
        return [
            PldPoint(
                period=row.week_start.isoformat(),
                subsystem=row.subsystem,
                pld_avg_brl_per_mwh=float(row.pld_avg_brl_per_mwh),
            )
            for row in session.scalars(
                weekly.order_by(PldWeekly.week_start, PldWeekly.subsystem)
            ).all()
        ]
    if granularity == "daily":
        daily = select(PldDaily).where(PldDaily.day >= first, PldDaily.day <= last)
        if subsystem:
            daily = daily.where(PldDaily.subsystem == subsystem)
        return [
            _stats_point(row.day, row)
            for row in session.scalars(daily.order_by(PldDaily.day, PldDaily.subsystem)).all()
        ]
    monthly = select(PldMonthly).where(PldMonthly.month >= first, PldMonthly.month <= last)
    if subsystem:
        monthly = monthly.where(PldMonthly.subsystem == subsystem)
    return [
        _stats_point(row.month, row)
        for row in session.scalars(monthly.order_by(PldMonthly.month, PldMonthly.subsystem)).all()
    ]


def _stats_point(period: date, row: PldDaily | PldMonthly) -> PldPoint:
    return PldPoint(
        period=period.isoformat(),
        subsystem=row.subsystem,
        pld_avg_brl_per_mwh=float(row.pld_avg_brl_per_mwh),
        pld_min_brl_per_mwh=_float(row.pld_min_brl_per_mwh),
        pld_max_brl_per_mwh=_float(row.pld_max_brl_per_mwh),
        pld_std_brl_per_mwh=_float(row.pld_std_brl_per_mwh),
        hours_at_floor=row.hours_at_floor,
        hours_at_cap=row.hours_at_cap,
        hours=row.hours,
    )


def _years(session: SessionDep) -> list[int]:
    rows = session.scalars(select(func.extract("year", ReliabilityMonthly.month)).distinct()).all()
    return sorted(int(year) for year in rows)


def _aggregate(
    session: SessionDep, year: int, cnpj: str | None
) -> dict[str, ReliabilityDistributor]:
    """Per distributor: the monthly DEC and FEC of its sets weighted by consumers, summed over
    the months of the year (the annual indicator), and the consumer-weighted annual limits."""
    weight = func.coalesce(ReliabilityMonthly.consumers, 0)
    stmt = (
        select(
            ReliabilityMonthly.distributor_cnpj,
            ReliabilityMonthly.month,
            func.sum(ReliabilityMonthly.dec_hours * weight),
            func.sum(ReliabilityMonthly.fec_count * weight),
            func.sum(ReliabilityMonthly.dec_limit_hours * weight),
            func.sum(ReliabilityMonthly.fec_limit_count * weight),
            func.sum(weight),
            func.count(),
        )
        .where(
            ReliabilityMonthly.month >= date(year, 1, 1),
            ReliabilityMonthly.month < date(year + 1, 1, 1),
            ReliabilityMonthly.dec_hours.is_not(None),
        )
        .group_by(ReliabilityMonthly.distributor_cnpj, ReliabilityMonthly.month)
    )
    if cnpj:
        stmt = stmt.where(ReliabilityMonthly.distributor_cnpj == cnpj)
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for code, _month, dec, fec, dec_limit, fec_limit, consumers, sets in session.execute(
        stmt
    ).all():
        consumers_f = float(consumers or 0)
        if consumers_f <= 0:
            continue
        item = totals[code]
        item["dec"] += float(dec or 0) / consumers_f
        item["fec"] += float(fec or 0) / consumers_f
        item["dec_limit"] = float(dec_limit or 0) / consumers_f  # last month wins (same annual)
        item["fec_limit"] = float(fec_limit or 0) / consumers_f
        item["months"] += 1
        item["consumers"] = consumers_f
        item["sets"] = max(item["sets"], float(sets))
    acronyms = dict(session.execute(select(Distributor.cnpj, Distributor.acronym)).all())
    return {
        code: ReliabilityDistributor(
            cnpj=code,
            acronym=acronyms.get(code, code),
            consumers=int(values["consumers"]),
            dec_hours=round(values["dec"], 2),
            fec_count=round(values["fec"], 2),
            dec_limit_hours=round(values["dec_limit"], 2) or None,
            fec_limit_count=round(values["fec_limit"], 2) or None,
            months_with_data=int(values["months"]),
            sets=int(values["sets"]),
        )
        for code, values in totals.items()
    }


@router.get("/v1/reliability", response_model=ReliabilityResponse)
def reliability(
    session: SessionDep,
    cache: PublicCacheDep,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> ReliabilityResponse:
    """DEC (hours) and FEC (interruptions) per distributor for a year, worst first.

    Annual values add the monthly indicators; a year in progress shows the months published so
    far, and `months_with_data` says how many.
    """

    def load() -> ReliabilityResponse:
        years = _years(session)
        if not years:
            raise HTTPException(status_code=404, detail="No reliability data loaded yet.")
        chosen = year or years[-1]
        items = sorted(_aggregate(session, chosen, None).values(), key=lambda i: -i.dec_hours)
        return ReliabilityResponse(
            year=chosen,
            years=years,
            months_with_data=max((item.months_with_data for item in items), default=0),
            source=_source(session, "aneel-reliability"),
            items=items,
        )

    return cache.get_or_set(load)


@router.get("/v1/reliability/{cnpj}", response_model=ReliabilityDetailResponse)
def reliability_detail(
    cnpj: str,
    session: SessionDep,
    cache: PublicCacheDep,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> ReliabilityDetailResponse:
    """Monthly DEC and FEC of one distributor (consumer-weighted over its electrical sets)."""

    def load() -> ReliabilityDetailResponse:
        years = _years(session)
        chosen = year or (years[-1] if years else datetime.now(UTC).year)
        summary = _aggregate(session, chosen, cnpj).get(cnpj)
        if summary is None:
            raise HTTPException(status_code=404, detail="Distributor not found for that year.")
        weight = func.coalesce(ReliabilityMonthly.consumers, 0)
        rows = session.execute(
            select(
                ReliabilityMonthly.month,
                func.sum(ReliabilityMonthly.dec_hours * weight),
                func.sum(ReliabilityMonthly.fec_count * weight),
                func.sum(weight),
            )
            .where(
                ReliabilityMonthly.distributor_cnpj == cnpj,
                ReliabilityMonthly.month >= date(chosen, 1, 1),
                ReliabilityMonthly.month < date(chosen + 1, 1, 1),
                ReliabilityMonthly.dec_hours.is_not(None),
            )
            .group_by(ReliabilityMonthly.month)
            .order_by(ReliabilityMonthly.month)
        ).all()
        points = [
            ReliabilityMonthPoint(
                month=month,
                consumers=int(consumers),
                dec_hours=round(float(dec or 0) / float(consumers), 2),
                fec_count=round(float(fec or 0) / float(consumers), 2),
            )
            for month, dec, fec, consumers in rows
            if consumers
        ]
        return ReliabilityDetailResponse(
            year=chosen,
            years=years,
            distributor=summary,
            source=_source(session, "aneel-reliability"),
            points=points,
        )

    return cache.get_or_set(load)
