# 7. The curtailment forecast is published through the warehouse, not called over HTTP

- **Status:** Accepted
- **Scope:** spillway-forecast, spillway-api, spillway-web, spillway-infra

## Context

`spillway-forecast` trains a gradient boosting model (MLflow-tracked) on the silver layer of
`spillway-data` and scores the risk of wind/solar curtailment for the next 168 hours. A first
design had `spillway-api` proxy `GET /v1/forecast` to the forecast service over HTTP. That makes
the API depend on a second process at request time: its latency includes model scoring, an ML
outage becomes an API outage, scikit-learn would have to ship in the API image, and a forecast
shown to a member cannot be reproduced afterwards.

## Decision

- Forecasts are produced by a **batch job**: `spillway publish` (in `spillway-forecast`) loads the
  trained model, builds features from the silver layer, scores every series for the next 168 h
  and writes the result to Postgres **in one transaction**. Training is a separate, manual (about
  monthly) step.
- The tables follow the contract of ADR 0001: `spillway-api` owns the schema (migration `0007`),
  the producer only writes rows:
  - `forecast_model`: MLflow run id, version, training date, data period per source, row and
    series counts, metrics (baseline vs model, with bootstrap intervals), calibration bins,
    feature importance and the model card / limitations as JSONB; one row is `is_current`.
  - `forecast_run`: one scoring batch (`origin_ts`, `data_through`, `status` `loading|ready`),
    unique per model and origin, so publishing twice is idempotent.
  - `forecast_hourly`: risk probability and expected curtailment % per run, source, scope and
    hour; the primary key is also the read path.
  The run is inserted as `loading`, the hourly rows are copied and the run flips to `ready` inside
  the same transaction, so readers only ever see complete runs. The publisher keeps the last 14
  runs and deletes older ones (`forecast_hourly` cascades).
- `spillway-api` only **reads** the latest `ready` run: `GET /v1/forecast` (adds `generated_at`,
  `origin_ts`, `model_version`, `data_through`, `stale_days` so the portal can show how old a
  forecast is), `GET /v1/forecast/scopes` and `GET /v1/forecast/model` (training results). Answers
  are cached for a short TTL; `404` means nothing has been published yet. The API has no
  `FORECAST_URL`, no HTTP call to the ML service and no ML dependency. `spillway-web` calls only
  the API.
- The publisher connects as its own database role, `energia_forecast`: `SELECT` on the
  warehouse tables, `INSERT, DELETE` on the three forecast tables and `UPDATE` on exactly
  `forecast_model.is_current` and `forecast_run.status` (needed to flip the run and the current
  model). It cannot read member data. The grants live in `spillway-infra` (`create-db-users.sql`),
  and the CI guard classifies the new tables.
- **Scheduling.** In production a scheduled job (daily, after the monthly `spillway-data`
  publish and then every day) runs `spillway publish`. Retraining stays manual / monthly because it
  takes about 20 minutes and its metrics need a human to read them. The forecast service's own
  FastAPI (`spillway serve`) remains only as a debugging tool.

## Consequences

- The API keeps serving the last forecast, with its age, when the ML job is down; response time
  does not depend on scoring.
- Every forecast is versioned (model, origin, creation time) and auditable.
- The API image stays free of numpy/scikit-learn/joblib.
- A forecast is as fresh as the last job run and the last month in the lake: the data is
  published monthly, so `stale_days` can be large and the portal says so.
- The producer needs a database credential and a scheduler, and the first deploy needs three
  steps in order: API migration, infra role, then the job.
