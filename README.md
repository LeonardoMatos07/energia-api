# energia-api

HTTP API of the energy data platform. In Phase 1 it serves the public wind and solar
*curtailment* monitor for Brazil: series per SIN/subsystem and state, plants, ranking, monthly
reports and the newsletter, plus admin routes.

This repository **owns the Postgres schema** (Alembic). The contract between the repositories
is in [`docs/phase1-contract.md`](docs/phase1-contract.md): routes, payloads, tables and
variables are defined there.

| Repository | Relationship with the API |
|---|---|
| `energia-data` | Airflow: writes the tables and calls `POST /v1/admin/revalidate` after each load |
| `energia-web` | Next.js: calls the API from the server; types generated from `app.export_openapi` |

## Stack

Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL (psycopg 3), Pydantic Settings,
PyJWT (Cognito), boto3 (SES). Tooling: `uv`, `ruff`, `mypy` (strict), `pytest`.

## Running locally

Requirements: [uv](https://docs.astral.sh/uv/) and the `energia-data` Postgres
(`localhost:5433`, database `warehouse`).

```sh
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

`uv sync` creates `.venv` with every dependency, dev tools included. `alembic upgrade head` is
only needed when the schema has not been applied yet (see Migrations).

Interactive docs at `http://localhost:8000/docs`. To export the OpenAPI document used by
`energia-web`:

```sh
uv run python -m app.export_openapi openapi.json
```

## Environment variables

Read from the environment or from `.env` (see [`.env.example`](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `local` | `local` or `prod`. In `prod`, e-mail goes through SES and Cognito is required |
| `DATABASE_URL` | `postgresql+psycopg://warehouse:warehouse@localhost:5433/warehouse` | `postgresql://` is accepted |
| `WEB_URL` | `http://localhost:3000` | e-mail links, CORS and revalidation target |
| `REVALIDATE_SECRET` | empty | sent to `energia-web` in `x-revalidate-secret`; empty = web is not called |
| `REVALIDATE_TIMEOUT_SECONDS` | `5` | timeout of the call to `energia-web` |
| `COGNITO_REGION` | `sa-east-1` | |
| `COGNITO_USER_POOL_ID` | empty | required in `prod` |
| `COGNITO_APP_CLIENT_IDS` | empty | accepted clients, comma separated (web and M2M); required in `prod` |
| `AUTH_DISABLED` | `false` | opens `/v1/admin/*` without a token; **only** with `ENVIRONMENT=local` |
| `EMAIL_SENDER` | `Energia <no-reply@localhost>` | newsletter sender |
| `SES_REGION` | `sa-east-1` | |
| `CACHE_TTL_SECONDS` | `300` | in-memory cache of the public routes (0 disables it) |
| `NEWSLETTER_RATE_LIMIT_PER_MINUTE` | `5` | per IP and per instance |
| `TRUST_X_FORWARDED_FOR` | `false` | `true` only behind the ALB (uses the rightmost entry) |
| `WEB_API_SECRET` | empty | secret shared with the `energia-web` server; with it, the IP in `X-Client-IP` counts for the newsletter limit |

## Migrations

The schema is defined in `app/models.py` and versioned in `migrations/` (Alembic). No other
repository creates or alters tables.

- New migration: change `app/models.py`, run
  `uv run alembic revision --autogenerate -m "description"` and review the generated file.
- Apply: `uv run alembic upgrade head` (uses `DATABASE_URL`).
- In the full local environment, the `migrate` service of the `energia-data`
  `docker-compose.yml` builds this repository's image and runs `alembic upgrade head` against
  `warehouse-db` before Airflow starts.
- In production, the migration is a one-off task with the same image (`alembic upgrade head`);
  it does not run when the API starts.

## Tests and checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

By default the tests use in-memory SQLite. To run them on Postgres (which adds the migration
round trip and the model/migration drift check), point `TEST_DATABASE_URL` at a **disposable**
database: the tests truncate the tables and run `downgrade base`.

```sh
TEST_DATABASE_URL=postgresql+psycopg://warehouse:warehouse@localhost:5433/energia_api_test uv run pytest
```

## Authentication

Public routes have no authentication. `/v1/admin/*` requires a Cognito JWT *access token*
(RS256, validated against the pool JWKS, pool `iss`, `token_use = access`, `client_id` in
`COGNITO_APP_CLIENT_IDS`) that has:

- **person (admin)**: the `admin` group in `cognito:groups`. `energia-web` handles the login
  (OAuth with PKCE) and calls the API from the server with the user's token;
- **machine (`energia-data`)**: the `energia-api/admin` scope, obtained through *client
  credentials* on the M2M app client.

Missing or invalid token: 401. Valid token without the group or the scope: 403.

## Revalidation (data → API → web)

1. After each load, `energia-data` calls `POST /v1/admin/revalidate` with the machine token and
   `{"tags": ["curtailment", "plants"]}`.
2. The API clears its in-memory cache and calls `POST {WEB_URL}/api/revalidate` with the
   `x-revalidate-secret: {REVALIDATE_SECRET}` header and the same tags.
3. `energia-web` calls `revalidateTag` for each tag.

The response says whether the web was reached (`web_revalidated`, `detail`); a web failure does
not change the 200 status. Saving a report through `POST /v1/admin/reports` also clears the API
cache.

## Deployment

- `Dockerfile`: `python:3.12-slim` image with dependencies installed by `uv sync --frozen
  --no-dev`, unprivileged user, `uvicorn` on port 8000. The same image runs the migrations
  (`alembic upgrade head`).
- CI (`.github/workflows/ci.yml`), on every PR and push to `main`: ruff, mypy,
  `alembic upgrade head` and pytest against a Postgres 16 service, and the `openapi.json`
  export.
- Push to `main`: image pushed to ECR (`sa-east-1`, AWS authentication through OIDC, tags with
  the SHA and `latest`) and `aws ecs update-service --force-new-deployment` on the
  cluster/service from the repository variables `ECS_CLUSTER`/`ECS_SERVICE`.
- The cache and the newsletter limit live in memory, per instance; with several tasks, each one
  has its own.
