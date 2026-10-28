"""Curtailment risk forecast, read from the tables published by the ML batch job.

The model is trained and scored offline (repository ``spillway-forecast``); its publisher writes
``forecast_model`` / ``forecast_run`` / ``forecast_hourly`` in one transaction (ADR 0007). This
API only reads the latest *ready* run, so it never calls the model, has no ML dependencies and
keeps serving the last forecast (with its age) when the job is down.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import ForecastHourly, ForecastModel, ForecastRun
from app.schemas import (
    ForecastModelResponse,
    ForecastPoint,
    ForecastResponse,
    ForecastScope,
)

router = APIRouter(prefix="/v1/forecast", tags=["forecast"])

ForecastSource = Literal["wind", "solar"]
FORECAST_CACHE_CONTROL = "public, max-age=300, s-maxage=600, stale-while-revalidate=3600"
# Probability cut-offs of the human-readable level (not calibrated decision thresholds).
LEVEL_HIGH = 0.66
LEVEL_MEDIUM = 0.33
RISK_DEFINITION = "hour with curtailment >= 10% of potential output"


def risk_level(probability: float) -> Literal["baixo", "medio", "alto"]:
    if probability >= LEVEL_HIGH:
        return "alto"
    if probability >= LEVEL_MEDIUM:
        return "medio"
    return "baixo"


def _latest_run(session: Session) -> tuple[ForecastRun, ForecastModel] | None:
    row = session.execute(
        select(ForecastRun, ForecastModel)
        .join(ForecastModel, ForecastModel.id == ForecastRun.model_id)
        .where(ForecastRun.status == "ready")
        .order_by(ForecastRun.origin_ts.desc(), ForecastRun.id.desc())
        .limit(1)
    ).first()
    return None if row is None else (row[0], row[1])


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.get(
    "",
    response_model=ForecastResponse,
    responses={404: {"description": "Nothing published for this scope and source"}},
)
def forecast(
    session: SessionDep,
    cache: PublicCacheDep,
    response: Response,
    scope: Annotated[str, Query(min_length=1, max_length=3, examples=["NE"])],
    source: ForecastSource = "wind",
    days: Annotated[int, Query(ge=1, le=7)] = 7,
) -> ForecastResponse:
    response.headers["Cache-Control"] = FORECAST_CACHE_CONTROL
    scope = scope.upper()

    def load() -> ForecastResponse | None:
        latest = _latest_run(session)
        if latest is None:
            return None
        run, model = latest
        rows = session.scalars(
            select(ForecastHourly)
            .where(
                ForecastHourly.run_id == run.id,
                ForecastHourly.source == source,
                ForecastHourly.scope == scope,
            )
            .order_by(ForecastHourly.hour_ts)
            .limit(days * 24)
        ).all()
        if not rows:
            return None
        points = []
        for row in rows:
            probability = float(row.risk_probability)
            points.append(
                ForecastPoint(
                    ts=row.hour_ts.isoformat(),
                    risk_probability=probability,
                    expected_curtailment_pct=float(row.expected_curtailment_pct),
                    level=risk_level(probability) if row.applicable else "baixo",
                    applicable=row.applicable,
                )
            )
        return ForecastResponse(
            scope=scope,
            source=source,
            risk_definition=RISK_DEFINITION,
            origin_ts=run.origin_ts.isoformat(),
            generated_at=_aware(run.created_at),
            data_through=run.data_through,
            stale_days=max(0, (datetime.now(UTC).date() - run.data_through).days),
            model_version=model.model_version,
            model_trained_at=_aware(model.trained_at),
            points=points,
        )

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="No forecast published for this scope.")
    return result


@router.get("/scopes", response_model=list[ForecastScope])
def forecast_scopes(session: SessionDep, cache: PublicCacheDep, response: Response) -> Any:
    response.headers["Cache-Control"] = FORECAST_CACHE_CONTROL

    def load() -> list[ForecastScope]:
        latest = _latest_run(session)
        if latest is None:
            return []
        pairs = session.execute(
            select(ForecastHourly.scope, ForecastHourly.source)
            .where(ForecastHourly.run_id == latest[0].id)
            .distinct()
            .order_by(ForecastHourly.source, ForecastHourly.scope)
        ).all()
        return [ForecastScope(scope=p[0], source=p[1]) for p in pairs]

    return cache.get_or_set(load)


@router.get(
    "/model",
    response_model=ForecastModelResponse,
    responses={404: {"description": "No model published yet"}},
)
def forecast_model(
    session: SessionDep, cache: PublicCacheDep, response: Response
) -> ForecastModelResponse:
    response.headers["Cache-Control"] = FORECAST_CACHE_CONTROL

    def load() -> ForecastModelResponse | None:
        current = session.scalars(
            select(ForecastModel).where(ForecastModel.is_current).limit(1)
        ).first()
        if current is None:
            return None
        run = session.scalars(
            select(ForecastRun)
            .where(ForecastRun.model_id == current.id, ForecastRun.status == "ready")
            .order_by(ForecastRun.origin_ts.desc())
            .limit(1)
        ).first()
        return ForecastModelResponse(
            model_version=current.model_version,
            trained_at=_aware(current.trained_at),
            data_period=current.data_period,
            n_series=current.n_series,
            n_rows=current.n_rows,
            metrics=current.metrics,
            calibration=current.calibration,
            feature_importance=current.feature_importance,
            model_card=current.model_card,
            last_run_at=_aware(run.created_at) if run else None,
            last_data_through=run.data_through if run else None,
        )

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="No forecast model published yet.")
    return result
