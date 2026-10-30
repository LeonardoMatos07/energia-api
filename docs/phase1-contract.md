# Phase 1 contract

Single source of truth between the four repositories. Any change here requires updating
`spillway-api` (the schema owner) first.

| Repository | Role |
|---|---|
| `spillway-data` | Airflow: ingests ONS data, computes the aggregates and **only writes** to the tables below |
| `spillway-api` | FastAPI: **schema owner** (Alembic), public reads, newsletter, admin |
| `spillway-web` | Next.js: public portal with ISR, admin area |
| `spillway-infra` | Terraform: AWS `sa-east-1` |

## Conventions

- Table, column, route and parameter names in **English**, `snake_case`.
- Code, documentation, logs and messages in English. Only text shown to site visitors (pages
  and newsletter e-mails) is in Portuguese, and so are the public site URLs.
- `source`: `wind`, `solar` or `total` (sum of both, aggregates only).
- `subsystem`: `N`, `NE`, `S`, `SE`; `SIN` is the national total (only in
  `subsystem_monthly_curtailment`).
- `state`: two-letter uppercase Brazilian state code.
- `month`: `date` holding the first day of the month (`2026-09-01`). In the API, `YYYY-MM`.
- Energy in MWh, money in BRL (`numeric`), percentages from 0 to 100 with 2 decimals.
- ONS timestamps are in Brasília time; `timestamptz` columns store UTC.
- Python: `uv`, `ruff` (lint and format), `mypy`, `pytest`. TypeScript: `pnpm`, ESLint,
  Prettier, Vitest. Commits follow Conventional Commits.
- Versions pinned exactly, published at least two weeks earlier.
- No comments in the code.

## Postgres schema (owner: `spillway-api`, Alembic)

Postgres only keeps what the portal reads. The detailed history lives in S3.

### `plant`
Plants and clusters. Plants dispatched individually (ONS "Type I") are `kind = 'plant'` with a
null `cluster_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | `text` PK | ONS id of the plant or cluster (`CJU_...`) |
| `kind` | `text` NOT NULL | `plant` or `cluster` |
| `name` | `text` NOT NULL | |
| `slug` | `text` NOT NULL UNIQUE | stable: `slug(name)-slug(id)`, e.g. `sao-fernando-4-rnesf4` |
| `source` | `text` NOT NULL | `wind` or `solar` |
| `state` | `char(2)` NOT NULL | |
| `subsystem` | `varchar(3)` NOT NULL | |
| `cluster_id` | `text` NULL FK → `plant.id` | |
| `ceg` | `text` NULL | ANEEL code |
| `capacity_mw` | `numeric(10,3)` NULL | sum of the generating units in operation |
| `connection_point` | `text` NULL | name of the grid connection point (clusters) |
| `operator` | `text` NULL | operating agent |
| `updated_at` | `timestamptz` NOT NULL DEFAULT now() | |

Indexes: `slug` (unique), `(state, source)`, `cluster_id`, and name search through
`lower(name)` (or `pg_trgm`, when available).

### `plant_monthly_curtailment`
One row per plant or cluster and month. For clusters the figures are the official ONS ones
(GNRa); for plants, the allocated GNRa (sum of the plants = cluster).

| Column | Type |
|---|---|
| `plant_id` | `text` FK → `plant.id` |
| `month` | `date` |
| `verified_generation_mwh` | `numeric(14,3)` NOT NULL |
| `curtailed_mwh` | `numeric(14,3)` NOT NULL |
| `curtailment_pct` | `numeric(6,2)` NULL (curtailed / (verified + curtailed) × 100) |
| `curtailed_rel_mwh`, `curtailed_cnf_mwh`, `curtailed_ene_mwh`, `curtailed_other_mwh` | `numeric(14,3)` NOT NULL |
| `estimated_loss_brl` | `numeric(16,2)` NULL |
| `price_basis` | `text` NULL (`PLD`, `CMO`, `PLD_FILLED`, `CMO_FILLED`: the prevailing basis in the month) |
| `restricted_hours` | `numeric(7,1)` NOT NULL |
| `state_avg_curtailment_pct` | `numeric(6,2)` NULL (state and source average in the month, for comparison) |
| `national_rank` | `integer` NULL (per source and month, by curtailed energy; `kind = 'plant'` only) |
| `state_rank` | `integer` NULL (per state, source and month; `kind = 'plant'` only) |
| `updated_at` | `timestamptz` NOT NULL DEFAULT now() |

PK `(plant_id, month)`. Indexes: `(month, national_rank)`, `(month, state_rank)`.

### Forecast tables (ADR 0007)

`spillway-forecast` (`spillway publish`, role `energia_forecast`) writes `forecast_model`, `forecast_run` and `forecast_hourly` in one transaction; the API only reads the latest run with `status = 'ready'`. Routes: `GET /v1/forecast?scope=&source=&days=`, `GET /v1/forecast/scopes`, `GET /v1/forecast/model` (public, short cache, `404` until something is published).

### `state_monthly_curtailment`
| Column | Type |
|---|---|
| `state` | `char(2)` |
| `source` | `text` (`wind`, `solar`, `total`) |
| `month` | `date` |
| `verified_generation_mwh`, `curtailed_mwh` | `numeric(16,3)` NOT NULL |
| `curtailment_pct` | `numeric(6,2)` NULL |
| `curtailed_rel_mwh`, `curtailed_cnf_mwh`, `curtailed_ene_mwh`, `curtailed_other_mwh` | `numeric(16,3)` NOT NULL |
| `estimated_loss_brl` | `numeric(16,2)` NULL |
| `affected_plants` | `integer` NOT NULL (plants with curtailment > 0) |
| `updated_at` | `timestamptz` NOT NULL DEFAULT now() |

PK `(state, source, month)`.

### `subsystem_monthly_curtailment`
Same columns as `state_monthly_curtailment`, with `subsystem` (`N`, `NE`, `S`, `SE`, `SIN`)
instead of `state`. PK `(subsystem, source, month)`.

### `load_run`
One row per pipeline load (read by `/v1/admin/load-runs`).

| Column | Type |
|---|---|
| `id` | `bigint` identity PK |
| `dataset` | `text` NOT NULL (source name, e.g. `wind_plant_curtailment`, or `aggregates`) |
| `period` | `date` NULL (processed month) |
| `source_file` | `text` NULL (source URL) |
| `checksum` | `text` NULL (sha256) |
| `row_count` | `integer` NULL |
| `started_at` | `timestamptz` NOT NULL |
| `finished_at` | `timestamptz` NULL |
| `status` | `text` NOT NULL (`running`, `success`, `failed`) |
| `detail` | `text` NULL (quality warnings or error) |

Index `(started_at DESC)`.

### `monthly_report`
| Column | Type |
|---|---|
| `id` | `bigint` identity PK |
| `month` | `date` NOT NULL |
| `title` | `text` NOT NULL |
| `slug` | `text` NOT NULL UNIQUE |
| `summary` | `text` NULL |
| `content` | `text` NOT NULL (Markdown) |
| `published_at` | `timestamptz` NULL (null = draft) |
| `created_at`, `updated_at` | `timestamptz` NOT NULL DEFAULT now() |

### `newsletter_subscriber`
| Column | Type |
|---|---|
| `id` | `bigint` identity PK |
| `email` | `text` NOT NULL UNIQUE (lowercase) |
| `consented_at` | `timestamptz` NOT NULL |
| `confirmed_at` | `timestamptz` NULL |
| `unsubscribed_at` | `timestamptz` NULL |
| `confirmation_token_hash` | `text` NULL (sha256 of the token; the token itself is never stored) |
| `confirmation_token_expires_at` | `timestamptz` NULL |
| `unsubscribe_token_hash` | `text` NOT NULL (one-click unsubscribe, never expires) |
| `signup_source` | `text` NULL |
| `created_at` | `timestamptz` NOT NULL DEFAULT now() |

Retention (LGPD, data minimization): on every `POST /v1/newsletter`, the API deletes the
addresses that never confirmed after the confirmation link expired (48 h) and the ones
unsubscribed for more than 30 days. The portal privacy policy (`/privacidade`) promises these
periods.

### `organization` and `app_user` (Phase 2 groundwork, created empty)
- `organization`: `id uuid` PK, `name text NOT NULL`, `created_at`.
- `app_user`: `id uuid` PK, `cognito_sub text UNIQUE NOT NULL`, `email text UNIQUE NOT NULL`,
  `name text NULL`, `organization_id uuid FK NULL`, `role text NOT NULL`
  (`owner`, `member`, `admin`), `created_at`.

### System generation tables (migration 0008)
Written by `spillway-data` (`system` pipeline). Power values are **average MW** over the hour
(ONS "MWmed", so MWh in an hour); energies are MWh. Hours are Brasilia time (`hour`) plus UTC
(`hour_utc`). `subsystem` uses the same codes as the other tables.
- `subsystem_hourly_balance`: PK `(hour, subsystem)`; `hydro_mw_avg`, `thermal_mw_avg`,
  `wind_mw_avg`, `solar_mw_avg`, `load_mw_avg`, `net_interchange_mw_avg` (nullable). Thermal
  includes nuclear. Subsystems `N`, `NE`, `S`, `SE`.
- `subsystem_monthly_generation`: PK `(month, subsystem, source)`; `source` is `hydro`,
  `thermal`, `wind` or `solar`; subsystem also has `SIN` (sum); `energy_mwh`, `avg_mw`,
  `peak_mw`, `share_pct` (share of the four sources).
- `power_plant`: the whole generation fleet (`id` PK, `name`, `slug` unique, `source_type` in
  `hydro|thermal|nuclear|wind|solar|other`, `fuel_type`, `state`, `subsystem`, `ceg`,
  `capacity_mw`, `updated_at`). `plant` stays the curtailment product (wind and solar only), so
  hydro and thermal plants do not fit it; wind and solar plants use the same id and slug rule.
- `plant_monthly_generation`: PK `(month, plant_id)`, FK `power_plant.id`; `energy_mwh`,
  `avg_mw`, `peak_mw`, `capacity_factor` (energy / (capacity x hours of the month), 0 to 1.05).

### Interchange and thermal dispatch (migration 0009)
- `interchange_hourly`: PK `(hour, origin, destination)`, `scope` (`national` between subsystems,
  `international` from `BR` to a country in upper case), `hour_utc`, `flow_mw_avg`,
  `scheduled_mw_avg`. **Positive flow means origin to destination**; the ONS reports national
  flows as origin/destination pairs (a negative value is a flow in the opposite direction) and
  international ones as the flow to the destination country (negative is an import).
- `interchange_monthly`: PK `(month, origin, destination)`; `energy_mwh` (net, signed),
  `scheduled_energy_mwh`, `avg_mw`, `peak_mw`, `min_mw`. The ONS dictionaries carry no
  transmission limit, so there is no "hours at limit" column.
- `thermal_dispatch_monthly`: PK `(month, plant_code, reason)`; `plant_code` is the planning code
  (`cod_usinaplanejamento`), plus `plant_name`, `ceg`, `fuel_type`, `subsystem`, `verified_mwh`,
  `programmed_mwh`. `reason` is one of `total`, `merit_order`,
  `merit_order_above_inflexibility`, `inflexibility`, `inflexibility_in_merit`,
  `inflexibility_pure`, `electrical_reason`, `energy_security`, `gfom`, `loss_replacement`,
  `export`, `power_reserve`, `gsub`, `unit_commitment`, `constrained_off`. Reasons **overlap**
  (merit order contains its above-inflexibility part; inflexibility is split in two), so only
  read them one at a time or against `total`.

### Hydrology tables (migration 0010)
Energy stored in reservoirs (EAR) is in **MW-month** (`_mw_month`), natural inflow energy (ENA) in
average MW (`_mw_avg`) and as a percentage of the long-term average (`_pct_mlt`); flows are in
m3/s (`_m3s`) and levels in metres (`_m`).
- `reservoir`: the register (`id` PK = ONS `res_id`, `name`, `slug` unique, `reservoir_type`,
  `planning_code`, `station_code`, `plant_name`, `ceg`, `subsystem`, `ree`, `basin`, `river`,
  `commissioned_on`, levels, volumes in hm3, `latitude`, `longitude`). Snapshot, upserted by `id`.
- `subsystem_storage_daily`: PK `(day, subsystem)`; `storage_max_mw_month`, `storage_mw_month`,
  `storage_pct` (0 to 100), `ena_gross_*`, `ena_storable_*`.
- `reservoir_daily`: PK `(day, reservoir_id)`, FK `reservoir.id`; the same storage and ENA columns
  (`storage_pct` is 0 to 200: a reservoir that spills is above its nominal maximum)
  per reservoir plus `useful_volume_pct`, `inflow_m3s`, `natural_inflow_m3s`, `outflow_m3s`,
  `turbined_m3s`, `spilled_m3s`, `upstream_level_m`, `downstream_level_m`. The ONS uses three
  reservoir codes: EAR and ENA use the planning code (`cod_resplanejamento`), the hydrological
  data use `id_reservatorio` (= `res_id`); the register maps one to the other.
- `plant_monthly_spilled_energy`: PK `(month, plant_code)` (`plant_code` is the ONS optimisation
  code `cod_usina`), `plant_name`, `subsystem`, `basin`, `river`, `agent`, `spilled_energy_mwh`,
  `spilled_turbinable_energy_mwh`. It does not join `power_plant` (different codes).

### How `spillway-data` writes
- `plant`: upsert by `id`.
- `*_monthly_curtailment`: for each processed month, deletes the rows of that `month` and
  inserts the new ones, in one transaction (idempotent).
- `load_run`: one row per source and month, plus one `aggregates` row per month.
- `power_plant`: upsert by `id`. `subsystem_monthly_generation`, `plant_monthly_generation`: same
  delete-and-insert by `month`. `subsystem_hourly_balance`: delete-and-insert by `hour` range.
- `interchange_hourly`: delete-and-insert by `hour` range; `interchange_monthly` and
  `thermal_dispatch_monthly`: by `month`.
- `reservoir`: upsert by `id`. `subsystem_storage_daily` and `reservoir_daily`: delete-and-insert
  by `day` range of the processed month; `plant_monthly_spilled_energy`: by `month`.
- Its own database user, with `SELECT, INSERT, UPDATE, DELETE` on these tables only.

## Data lake (S3)

```
bronze/ons/<source>/<partition>/<original file>   as published by ONS
silver/<source>/year=YYYY/month=MM/data.parquet    standardized (and modelled, for curtailment)
gold/<table>/year=YYYY/month=MM/data.parquet       the same aggregates as Postgres
```

## API (`spillway-api`)

Base `/v1`. JSON keys match the column names. `numeric` values are serialized as numbers.
Public routes: no authentication, with `Cache-Control: public, max-age=300, s-maxage=3600,
stale-while-revalidate=86400`. Errors use `{"detail": "..."}` (always a string, including the
422 validation errors). Months (`month`, `start`, `end`, `period`, `ranking_month`) go in and
out as `YYYY-MM`.

| Method | Route | Access | Returns |
|---|---|---|---|
| GET | `/v1/curtailment/subsystems?source=&start=&end=&subsystem=` | Public | `{"items": [subsystem_monthly_curtailment row]}` ordered by `month`, `subsystem`; `source` defaults to `total`; `start`/`end` as `YYYY-MM`; optional `subsystem` (`N`, `NE`, `S`, `SE`, `SIN`; all of them without it) |
| GET | `/v1/curtailment/states/{state}?source=&month=` | Public | `{"series": [state_monthly_curtailment rows], "ranking": [RankedPlant], "ranking_month": "YYYY-MM" or null}`; ranking of `month` (default: latest month with data for the state; `null` when there is none), top 20. `state` accepts lowercase; 404 when it is not a Brazilian state, 422 when it is not two letters |
| GET | `/v1/system/balance?subsystem=&from=&to=` | Public | `{"date_from", "date_to", "points": [subsystem_hourly_balance row]}`, hourly, at most 31 days (422 above); `from`/`to` as `YYYY-MM-DD`, default the last 7 days with data |
| GET | `/v1/system/generation?subsystem=&from=&to=` | Public | `{"points": [subsystem_monthly_generation row]}`; `subsystem` defaults to `SIN`; `from`/`to` as `YYYY-MM`, default the last 12 months |
| GET | `/v1/system/interchange?origin=&destination=&scope=&from=&to=` | Public | `{"points": [interchange_monthly row]}`; `from`/`to` as `YYYY-MM`, default the last 12 months |
| GET | `/v1/system/interchange/hourly?origin=&destination=&from=&to=` | Public | `{"date_from", "date_to", "points": [interchange_hourly row]}`, at most 31 days |
| GET | `/v1/system/thermal-dispatch?subsystem=&reason=&plant=&from=&to=` | Public | `{"points": [...]}` summed per month, subsystem and reason (`plants` = how many plants), or per plant when `plant` (a `plant_code`) is given |
| GET | `/v1/hydro/subsystems?subsystem=&from=&to=` | Public | `{"date_from", "date_to", "points": [subsystem_storage_daily row]}`; days as `YYYY-MM-DD`, default the last 90 days of data, at most 366 |
| GET | `/v1/hydro/reservoirs?subsystem=&basin=` | Public | `{"latest_day", "items": [reservoir + storage_pct and ena_storable_pct_mlt of the latest day]}` |
| GET | `/v1/hydro/reservoirs/{slug}?from=&to=` | Public | `{"reservoir", "points": [reservoir_daily row]}`; 404 for an unknown slug |
| GET | `/v1/hydro/spilled-energy?subsystem=&plant=&month=&limit=` | Public | `{"points": [plant_monthly_spilled_energy row]}` of one month (default the latest), largest turbinable energy first |
| GET | `/v1/plants/{slug}/generation?from=&to=` | Public | `{"plant": power_plant, "points": [plant_monthly_generation row]}`; 404 for an unknown slug |
| GET | `/v1/plants?search=&state=&source=&kind=&page=1&page_size=20` | Public | `{"items": [PlantSummary], "total": n, "page": p, "page_size": s}`; `page` ≥ 1, `page_size` from 1 to 100 |
| GET | `/v1/plants/{slug}` | Public | `{"plant": Plant, "cluster": PlantSummary|null, "cluster_plants": [PlantSummary], "series": [plant_monthly_curtailment rows]}` |
| GET | `/v1/ranking?month=&state=&source=&limit=20` | Public | `{"month": "YYYY-MM" or null, "items": [RankedPlant]}` (`limit` ≤ 100; `month` defaults to the latest month with data; `null` and an empty list when there is no data) |
| GET | `/v1/months` | Public | `{"months": ["YYYY-MM", ...]}` months with data, newest first |
| GET | `/v1/reports` | Public | `{"items": [ReportSummary]}` published only |
| GET | `/v1/reports/{slug}` | Public | `Report` (404 for drafts) |
| POST | `/v1/newsletter` | Public, rate limited (5/min per IP, see below) | `202 {"message": "..."}`; body `{"email", "consent": true, "source"?}`; always 202, even when already subscribed (does not leak who is on the list); 429 with `Retry-After: 60` over the limit |
| GET | `/v1/newsletter/confirm?token=` | Public | `{"confirmed": true}` or 400 |
| GET | `/v1/newsletter/unsubscribe?token=` | Public | `{"unsubscribed": true}` or 400 |
| GET | `/v1/admin/load-runs?limit=50` | Admin | `{"items": [load_run row]}`, newest first; `limit` from 1 to 500; `period` as `YYYY-MM` (or null) |
| GET | `/v1/admin/reports` | Admin | `{"items": [AdminReport]}`, all of them including drafts (`published_at` null), by `month` descending |
| POST | `/v1/admin/reports` | Admin | `200 AdminReport`; creates or updates by `slug`; body `{"month": "YYYY-MM", "title", "slug"?, "summary"?, "content", "publish": bool}`. Optional `slug` (`^[a-z0-9]+(-[a-z0-9]+)*$`, up to 200); generated from `title` when missing. `publish: true` keeps the original publication date; `false` turns it back into a draft |
| POST | `/v1/admin/revalidate` | Admin | optional body `{"tags": [...]}` (default: all; unknown tag = 422); clears the API cache and calls `spillway-web`. Always 200 `RevalidateResponse`, even when `spillway-web` fails |
| GET | `/health` | Internal | `{"status": "ok", "database": "ok"}` |

Payloads:
- `PlantSummary`: `id, kind, name, slug, source, state, subsystem, capacity_mw`
- `Plant`: `PlantSummary` + `cluster_id, ceg, connection_point, operator, updated_at`
- `RankedPlant`: `PlantSummary` + `rank, curtailed_mwh, curtailment_pct, estimated_loss_brl`
- `ReportSummary`: `month, title, slug, summary, published_at`; `Report`: + `content`;
  `AdminReport`: `Report` + `created_at, updated_at`
- `RevalidateResponse`: `{"tags": [...], "cache_cleared": true, "entries_removed": n,
  "web_revalidated": bool, "detail": "..." | null}`. `web_revalidated = false` with `detail`
  when `spillway-web` did not answer 2xx or `REVALIDATE_SECRET` is not set.

### Client IP for the newsletter limit

The 5/min limit is per IP and per API instance. The IP is chosen like this:

1. When `WEB_API_SECRET` is set and the request carries an equal `X-Web-Api-Secret`
   (constant-time comparison), the IP in `X-Client-IP` (valid IPv4 or IPv6) is used. This is
   how `spillway-web`, which calls the API from its server (every visitor leaves through the
   same Vercel IP), forwards the visitor IP.
2. Otherwise, with `TRUST_X_FORWARDED_FOR=true`, the rightmost `X-Forwarded-For` entry (the
   one the ALB appends).
3. Otherwise, the connection IP.

`X-Client-IP` without the right secret is ignored. The secret only exists on the API and
`spillway-web` servers; it never reaches the browser.

## Authentication

- Cognito User Pool, e-mail sign-in, mandatory MFA. `admin` group. Self sign-up disabled.
- **Admin (person)**: `spillway-web` signs in with OAuth + PKCE (Auth.js, Cognito provider),
  session in an `httpOnly` cookie; it calls the API from the server with the *access token*.
- **Machine (`spillway-data` → API)**: app client with *client credentials* and the
  `energia-api` resource server with the `energia-api/admin` scope.
- On `/v1/admin/*` the API accepts a Cognito JWT (RS256, pool JWKS, pool `iss`,
  `token_use = access`) that has the `admin` group in `cognito:groups` **or** the
  `energia-api/admin` scope.
- Local: `AUTH_DISABLED=true` opens the admin routes **only** with `ENVIRONMENT=local` (the API
  refuses to start with that flag in any other environment).

## Portal revalidation

1. After each load, `spillway-data` calls `POST {API_URL}/v1/admin/revalidate` with
   `{"tags": ["curtailment", "plants"]}`.
2. The API clears its own cache and calls `POST {WEB_URL}/api/revalidate` with the
   `x-revalidate-secret: {REVALIDATE_SECRET}` header and the same body.
3. `spillway-web` calls `revalidateTag` for each tag. Tags used by the `fetch` calls:
   `curtailment`, `plants`, `reports`.

## Local environment

| Service | Address |
|---|---|
| Postgres | `localhost:5433`, database `warehouse` (user/password `warehouse` in dev) |
| S3 (SeaweedFS) | `http://localhost:8333`, bucket `energy-lake` |
| API | `http://localhost:8000` |
| Web | `http://localhost:3000` |
| Airflow | `http://localhost:8080` |

Main variables: `DATABASE_URL` (API), `WAREHOUSE_DSN` (data), `API_URL`, `WEB_URL`,
`REVALIDATE_SECRET`, `WEB_API_SECRET` (API and web), `COGNITO_REGION`, `COGNITO_USER_POOL_ID`,
`COGNITO_APP_CLIENT_IDS`, `ENVIRONMENT` (`local` or `prod`).

## CI/CD (GitHub Actions)

- Every PR: lint, type checks and tests.
- Merge to `main` on `spillway-api` and `spillway-data`: Docker image pushed to ECR (AWS
  authentication through OIDC) and deployed.
- `spillway-web`: deployed by Vercel (one preview per PR); CI runs lint, types and tests.
- `spillway-infra`: `terraform plan` on PRs; `apply` on `main` with manual approval
  (`production` environment with required reviewers).
