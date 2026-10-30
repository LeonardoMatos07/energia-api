"""Grid register: transmission lines and substations (ONS), with the history of the register."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import Select, func, or_, select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import Substation, TransmissionLine
from app.schemas import (
    SubstationItem,
    SubstationListResponse,
    Subsystem,
    TransmissionLineItem,
    TransmissionLineListResponse,
)

router = APIRouter(prefix="/v1/grid", tags=["grid"])

STATE = Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$", examples=["BA"])]
AsOf = Annotated[
    date | None,
    Query(description="Register as of this date (default: the current version of each item)"),
]


def _as_of(stmt: Select[Any], model: Any, as_of: date | None) -> Select[Any]:
    if as_of is None:
        return stmt.where(model.valid_to.is_(None))
    return stmt.where(
        model.valid_from <= as_of, or_(model.valid_to.is_(None), model.valid_to > as_of)
    )


@router.get("/lines", response_model=TransmissionLineListResponse)
def grid_lines(
    session: SessionDep,
    cache: PublicCacheDep,
    voltage_kv: Annotated[float | None, Query(ge=0, le=1500)] = None,
    min_voltage_kv: Annotated[float | None, Query(ge=0, le=1500)] = None,
    state: STATE = None,
    subsystem: Subsystem | None = None,
    as_of: AsOf = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TransmissionLineListResponse:
    """Transmission lines. ``state`` and ``subsystem`` match either end of the line."""

    def load() -> TransmissionLineListResponse:
        filters: list[Any] = []
        if voltage_kv is not None:
            filters.append(TransmissionLine.voltage_kv == voltage_kv)
        if min_voltage_kv is not None:
            filters.append(TransmissionLine.voltage_kv >= min_voltage_kv)
        if state is not None:
            wanted = state.upper()
            filters.append(
                or_(TransmissionLine.from_state == wanted, TransmissionLine.to_state == wanted)
            )
        if subsystem is not None:
            filters.append(
                or_(
                    TransmissionLine.from_subsystem == subsystem,
                    TransmissionLine.to_subsystem == subsystem,
                )
            )
        base = _as_of(select(TransmissionLine), TransmissionLine, as_of).where(*filters)
        total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = session.scalars(
            base.order_by(TransmissionLine.voltage_kv.desc(), TransmissionLine.equipment_code)
            .limit(page_size)
            .offset((page - 1) * page_size)
        ).all()
        return TransmissionLineListResponse(
            as_of=as_of,
            total=total,
            page=page,
            page_size=page_size,
            items=[TransmissionLineItem.model_validate(row) for row in rows],
        )

    return cache.get_or_set(load)


@router.get("/substations", response_model=SubstationListResponse)
def grid_substations(
    session: SessionDep,
    cache: PublicCacheDep,
    voltage_kv: Annotated[float | None, Query(ge=0, le=1500)] = None,
    min_voltage_kv: Annotated[float | None, Query(ge=0, le=1500)] = None,
    state: STATE = None,
    subsystem: Subsystem | None = None,
    as_of: AsOf = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SubstationListResponse:
    def load() -> SubstationListResponse:
        filters: list[Any] = []
        if voltage_kv is not None:
            filters.append(Substation.voltage_kv == voltage_kv)
        if min_voltage_kv is not None:
            filters.append(Substation.voltage_kv >= min_voltage_kv)
        if state is not None:
            filters.append(Substation.state == state.upper())
        if subsystem is not None:
            filters.append(Substation.subsystem == subsystem)
        base = _as_of(select(Substation), Substation, as_of).where(*filters)
        total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = session.scalars(
            base.order_by(Substation.voltage_kv.desc(), Substation.station_id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        ).all()
        return SubstationListResponse(
            as_of=as_of,
            total=total,
            page=page,
            page_size=page_size,
            items=[SubstationItem.model_validate(row) for row in rows],
        )

    return cache.get_or_set(load)
