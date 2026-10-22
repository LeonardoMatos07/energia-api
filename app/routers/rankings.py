from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.queries import grouped_ranking
from app.schemas import MONTH_PATTERN, AggregateSource, GroupRankingResponse, parse_month

router = APIRouter(prefix="/v1/rankings", tags=["rankings"])

MonthQuery = Annotated[str | None, Query(pattern=MONTH_PATTERN, examples=["2026-09"])]
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _ranking(
    group: str,
    session: SessionDep,
    cache: PublicCacheDep,
    source: AggregateSource,
    start: str | None,
    end: str | None,
    limit: int,
) -> GroupRankingResponse:
    first = parse_month(start) if start is not None else None
    last = parse_month(end) if end is not None else None
    if first is not None and last is not None and first > last:
        raise HTTPException(status_code=422, detail="start must not be after end.")

    def load() -> GroupRankingResponse:
        return GroupRankingResponse(
            group=group,
            source=source,
            start=first,
            end=last,
            items=grouped_ranking(session, group, source, first, last, limit),
        )

    return cache.get_or_set(load)


@router.get("/operators", response_model=GroupRankingResponse)
def operators(
    session: SessionDep,
    cache: PublicCacheDep,
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> GroupRankingResponse:
    return _ranking("operator", session, cache, source, start, end, limit)


@router.get("/connection-points", response_model=GroupRankingResponse)
def connection_points(
    session: SessionDep,
    cache: PublicCacheDep,
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> GroupRankingResponse:
    return _ranking("connection_point", session, cache, source, start, end, limit)
