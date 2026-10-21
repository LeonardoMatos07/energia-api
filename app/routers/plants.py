from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import Plant, PlantMonthlyCurtailment, SubsystemMonthlyCurtailment
from app.queries import latest_plant_month, plant_ranking
from app.schemas import (
    MONTH_PATTERN,
    AggregateSource,
    Kind,
    MonthsResponse,
    PlantCurtailment,
    PlantDetailResponse,
    PlantListResponse,
    PlantSummary,
    RankingResponse,
    Source,
    parse_month,
)
from app.schemas import Plant as PlantSchema

router = APIRouter(prefix="/v1", tags=["plants"])


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/plants", response_model=PlantListResponse)
def list_plants(
    session: SessionDep,
    cache: PublicCacheDep,
    search: Annotated[str | None, Query(max_length=100)] = None,
    state: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    source: Source | None = None,
    kind: Kind | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PlantListResponse:
    def load() -> PlantListResponse:
        filters = []
        if search and search.strip():
            pattern = f"%{_escape_like(search.strip().lower())}%"
            filters.append(
                or_(
                    func.lower(Plant.name).like(pattern, escape="\\"),
                    func.lower(Plant.id).like(pattern, escape="\\"),
                )
            )
        if state is not None:
            filters.append(Plant.state == state.upper())
        if source is not None:
            filters.append(Plant.source == source)
        if kind is not None:
            filters.append(Plant.kind == kind)

        total = session.scalar(select(func.count()).select_from(Plant).where(*filters)) or 0
        rows = session.scalars(
            select(Plant)
            .where(*filters)
            .order_by(Plant.name, Plant.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return PlantListResponse(
            items=[PlantSummary.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    return cache.get_or_set(load)


@router.get("/plants/{slug}", response_model=PlantDetailResponse)
def get_plant(slug: str, session: SessionDep, cache: PublicCacheDep) -> PlantDetailResponse:
    def load() -> PlantDetailResponse | None:
        plant = session.scalars(select(Plant).where(Plant.slug == slug)).first()
        if plant is None:
            return None
        cluster = session.get(Plant, plant.cluster_id) if plant.cluster_id else None
        cluster_id = plant.id if plant.kind == "cluster" else plant.cluster_id
        members = (
            session.scalars(
                select(Plant).where(Plant.cluster_id == cluster_id).order_by(Plant.name, Plant.id)
            ).all()
            if cluster_id
            else []
        )
        series = session.scalars(
            select(PlantMonthlyCurtailment)
            .where(PlantMonthlyCurtailment.plant_id == plant.id)
            .order_by(PlantMonthlyCurtailment.month)
        ).all()
        return PlantDetailResponse(
            plant=PlantSchema.model_validate(plant),
            cluster=PlantSummary.model_validate(cluster) if cluster else None,
            cluster_plants=[PlantSummary.model_validate(m) for m in members],
            series=[PlantCurtailment.model_validate(s) for s in series],
        )

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="Plant not found.")
    return result


@router.get("/ranking", response_model=RankingResponse)
def ranking(
    session: SessionDep,
    cache: PublicCacheDep,
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN, examples=["2026-09"])] = None,
    state: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    source: AggregateSource = "total",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RankingResponse:
    def load() -> RankingResponse:
        state_upper = state.upper() if state else None
        ranking_month = parse_month(month) if month is not None else latest_plant_month(session)
        if ranking_month is None:
            return RankingResponse(month=None, items=[])
        return RankingResponse(
            month=ranking_month,
            items=plant_ranking(session, ranking_month, state_upper, source, limit),
        )

    return cache.get_or_set(load)


@router.get("/months", response_model=MonthsResponse, tags=["curtailment"])
def months(session: SessionDep, cache: PublicCacheDep) -> MonthsResponse:
    def load() -> MonthsResponse:
        stmt = select(SubsystemMonthlyCurtailment.month).union(
            select(PlantMonthlyCurtailment.month)
        )
        values = sorted(set(session.scalars(stmt).all()), reverse=True)
        return MonthsResponse(months=values)

    return cache.get_or_set(load)
