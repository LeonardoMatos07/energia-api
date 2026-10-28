from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ForecastHourly, ForecastModel, ForecastRun
from app.routers.forecast import FORECAST_CACHE_CONTROL, risk_level

ORIGIN = datetime(2026, 10, 1)  # noqa: DTZ001 - naive local time on purpose


def add_model(session: Session, current: bool = True) -> ForecastModel:
    model = ForecastModel(
        mlflow_run_id="run-1" if current else "run-0",
        model_version="v1",
        trained_at=datetime(2026, 10, 28, 12, tzinfo=UTC),
        data_period={"wind": {"start": "2021-10-01", "end": "2026-09-30"}},
        n_series=27,
        n_rows=1000,
        metrics={"test": {"hist_gbm": {"mae_pp": 10.0}}},
        calibration={"hist_gbm": [{"mean_pred": 0.1, "obs": 0.12, "n": 5}]},
        feature_importance={"lag": 0.1},
        model_card={"limitations": ["sem clima"]},
        is_current=current,
    )
    session.add(model)
    session.flush()
    return model


def add_run(
    session: Session, model: ForecastModel, origin: datetime = ORIGIN, status: str = "ready"
) -> ForecastRun:
    run = ForecastRun(
        model_id=model.id,
        origin_ts=origin,
        data_through=origin.date() - timedelta(days=1),
        horizon_hours=168,
        status=status,
    )
    session.add(run)
    session.flush()
    for h in range(168):
        session.add(
            ForecastHourly(
                run_id=run.id,
                source="wind",
                scope="NE",
                hour_ts=origin + timedelta(hours=h),
                risk_probability=0.8 if h % 24 == 12 else 0.1,
                expected_curtailment_pct=20.5,
                applicable=True,
            )
        )
    session.commit()
    return run


def test_empty_state_is_404(client: TestClient) -> None:
    assert client.get("/v1/forecast", params={"scope": "NE"}).status_code == 404
    assert client.get("/v1/forecast/model").status_code == 404
    assert client.get("/v1/forecast/scopes").json() == []


def test_serves_latest_ready_run(client: TestClient, session: Session) -> None:
    model = add_model(session)
    add_run(session, model, ORIGIN - timedelta(days=1))
    add_run(session, model, ORIGIN)
    add_run(session, model, ORIGIN + timedelta(days=1), status="loading")  # never visible
    response = client.get("/v1/forecast", params={"scope": "ne", "days": 2})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == FORECAST_CACHE_CONTROL
    body = response.json()
    assert body["scope"] == "NE" and body["origin_ts"] == "2026-10-01T00:00:00"
    assert len(body["points"]) == 48
    assert body["points"][12]["level"] == "alto" and body["points"][0]["level"] == "baixo"
    assert body["model_version"] == "v1" and body["data_through"] == "2026-09-30"
    assert "generated_at" in body and body["stale_days"] >= 0


def test_unknown_series_and_validation(client: TestClient, session: Session) -> None:
    add_run(session, add_model(session))
    assert client.get("/v1/forecast", params={"scope": "XX"}).status_code == 404
    assert client.get("/v1/forecast", params={"scope": "NE", "source": "solar"}).status_code == 404
    assert client.get("/v1/forecast", params={"scope": "NE", "days": 9}).status_code == 422


def test_model_endpoint_and_scopes(client: TestClient, session: Session) -> None:
    model = add_model(session)
    add_model(session, current=False)
    add_run(session, model)
    body = client.get("/v1/forecast/model").json()
    assert body["model_version"] == "v1" and body["n_series"] == 27
    assert body["metrics"]["test"]["hist_gbm"]["mae_pp"] == 10.0
    assert body["last_data_through"] == "2026-09-30"
    assert client.get("/v1/forecast/scopes").json() == [{"scope": "NE", "source": "wind"}]


def test_response_is_cached(app_factory: Callable[..., TestClient], session: Session) -> None:
    client = app_factory()
    model = add_model(session)
    add_run(session, model)
    first = client.get("/v1/forecast", params={"scope": "NE"}).json()
    add_run(session, model, ORIGIN + timedelta(days=1))
    again = client.get("/v1/forecast", params={"scope": "NE"}).json()
    assert again == first  # still within the TTL: no new query


def test_risk_level_bands() -> None:
    assert [risk_level(p) for p in (0.0, 0.33, 0.65, 0.66, 1.0)] == [
        "baixo",
        "medio",
        "medio",
        "alto",
        "alto",
    ]
