from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from sqlalchemy import select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import StateMonthlyCurtailment, SubsystemMonthlyCurtailment
from app.queries import (
    SUBSYSTEM_SCOPES,
    curtailment_summary,
    hourly_profile,
    latest_plant_month,
    plant_ranking,
)
from app.schemas import (
    MONTH_PATTERN,
    AggregateSource,
    CurtailmentSummary,
    HourlyProfileResponse,
    StateCurtailment,
    StateCurtailmentResponse,
    SubsystemCurtailment,
    SubsystemCurtailmentResponse,
    SubsystemOrSin,
    parse_month,
)

router = APIRouter(prefix="/v1/curtailment", tags=["curtailment"])

STATES = frozenset(
    {
        "AC",
        "AL",
        "AM",
        "AP",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MG",
        "MS",
        "MT",
        "PA",
        "PB",
        "PE",
        "PI",
        "PR",
        "RJ",
        "RN",
        "RO",
        "RR",
        "RS",
        "SC",
        "SE",
        "SP",
        "TO",
    }
)
STATE_RANKING_LIMIT = 20

MonthQuery = Annotated[str | None, Query(pattern=MONTH_PATTERN, examples=["2026-09"])]


@router.get("/subsystems", response_model=SubsystemCurtailmentResponse)
def subsystem_curtailment(
    session: SessionDep,
    cache: PublicCacheDep,
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
    subsystem: Annotated[
        SubsystemOrSin | None,
        Query(description="Optional; without it, every subsystem and the SIN total."),
    ] = None,
) -> SubsystemCurtailmentResponse:
    def load() -> SubsystemCurtailmentResponse:
        stmt = (
            select(SubsystemMonthlyCurtailment)
            .where(SubsystemMonthlyCurtailment.source == source)
            .order_by(SubsystemMonthlyCurtailment.month, SubsystemMonthlyCurtailment.subsystem)
        )
        if start is not None:
            stmt = stmt.where(SubsystemMonthlyCurtailment.month >= parse_month(start))
        if end is not None:
            stmt = stmt.where(SubsystemMonthlyCurtailment.month <= parse_month(end))
        if subsystem is not None:
            stmt = stmt.where(SubsystemMonthlyCurtailment.subsystem == subsystem)
        rows = session.scalars(stmt).all()
        return SubsystemCurtailmentResponse(
            items=[SubsystemCurtailment.model_validate(r) for r in rows]
        )

    return cache.get_or_set(load)


@router.get("/states/{state}", response_model=StateCurtailmentResponse)
def state_curtailment(
    session: SessionDep,
    cache: PublicCacheDep,
    state: Annotated[str, Path(pattern=r"^[A-Za-z]{2}$")],
    source: AggregateSource = "total",
    month: MonthQuery = None,
) -> StateCurtailmentResponse:
    state = state.upper()
    if state not in STATES:
        raise HTTPException(status_code=404, detail="State not found.")

    def load() -> StateCurtailmentResponse:
        rows = session.scalars(
            select(StateMonthlyCurtailment)
            .where(
                StateMonthlyCurtailment.state == state,
                StateMonthlyCurtailment.source == source,
            )
            .order_by(StateMonthlyCurtailment.month)
        ).all()
        ranking_month = (
            parse_month(month) if month is not None else latest_plant_month(session, state)
        )
        ranking = (
            plant_ranking(session, ranking_month, state, source, STATE_RANKING_LIMIT)
            if ranking_month is not None
            else []
        )
        return StateCurtailmentResponse(
            series=[StateCurtailment.model_validate(r) for r in rows],
            ranking=ranking,
            ranking_month=ranking_month,
        )

    return cache.get_or_set(load)


@router.get("/summary", response_model=CurtailmentSummary)
def summary(
    session: SessionDep,
    cache: PublicCacheDep,
    scope: Annotated[
        str,
        Query(
            pattern=r"^[A-Za-z]{2,3}$",
            description="SIN, a subsystem (N, NE, S, SE) or a state code.",
            examples=["SIN"],
        ),
    ] = "SIN",
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
) -> CurtailmentSummary:
    scope = scope.upper()
    if scope not in SUBSYSTEM_SCOPES and scope not in STATES:
        raise HTTPException(status_code=404, detail="Scope not found.")
    first = parse_month(start) if start is not None else None
    last = parse_month(end) if end is not None else None
    if first is not None and last is not None and first > last:
        raise HTTPException(status_code=422, detail="start must not be after end.")

    def load() -> CurtailmentSummary:
        return curtailment_summary(session, scope, source, first, last)

    return cache.get_or_set(load)


@router.get("/hourly", response_model=HourlyProfileResponse)
def hourly(
    session: SessionDep,
    cache: PublicCacheDep,
    scope: SubsystemOrSin = "SIN",
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
) -> HourlyProfileResponse:
    first = parse_month(start) if start is not None else None
    last = parse_month(end) if end is not None else None
    if first is not None and last is not None and first > last:
        raise HTTPException(status_code=422, detail="start must not be after end.")

    def load() -> HourlyProfileResponse:
        return hourly_profile(session, scope, source, first, last)

    return cache.get_or_set(load)
