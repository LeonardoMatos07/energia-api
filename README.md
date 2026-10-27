# energy-api

HTTP API of the energy data platform. In Phase 1 it serves the public wind and solar
*curtailment* monitor for Brazil: series per SIN/subsystem and state, plants, ranking, monthly
reports and the newsletter, plus admin routes.

This repository **owns the Postgres schema** (Alembic). The contract between the repositories
is in [`docs/phase1-contract.md`](docs/phase1-contract.md): routes, payloads, tables and
variables are defined there.

| Repository | Relationship with the API |
|---|---|
| `energy-data` | Airflow: writes the tables and calls `POST /v1/admin/revalidate` after each load |
| `energy-web` | Next.js: calls the API from the server; types generated from `app.export_openapi` |

Design decisions shared by the four repositories are recorded as [ADRs](docs/adr/README.md).

## Stack

Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL (psycopg 3), Pydantic Settings,
PyJWT (Cognito), boto3 (SES). Tooling: `uv`, `ruff`, `mypy` (strict), `pytest`.

## Running locally

Requirements: [uv](https://docs.astral.sh/uv/) and the `energy-data` Postgres
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
`energy-web`:

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
| `REVALIDATE_SECRET` | empty | sent to `energy-web` in `x-revalidate-secret`; empty = web is not called |
| `REVALIDATE_TIMEOUT_SECONDS` | `5` | timeout of the call to `energy-web` |
| `COGNITO_REGION` | `sa-east-1` | |
| `COGNITO_USER_POOL_ID` | empty | required in `prod` |
| `COGNITO_APP_CLIENT_IDS` | empty | accepted clients, comma separated (web and M2M); required in `prod` |
| `AUTH_DISABLED` | `false` | opens `/v1/admin/*` without a token; **only** with `ENVIRONMENT=local` |
| `EMAIL_SENDER` | `Spillway <no-reply@localhost>` | sender of every e-mail (must be a verified sender in your provider) |
| `EMAIL_BACKEND` | empty | `console` (logs the e-mail, local default), `smtp` or `ses`; empty = `ses` in `prod`, `console` otherwise |
| `SES_REGION` | `sa-east-1` | |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS` | empty, `587`, empty, empty, `true` | used when `EMAIL_BACKEND=smtp`; port 465 uses implicit TLS |
| `CACHE_TTL_SECONDS` | `300` | in-memory cache of the public routes (0 disables it) |
| `NEWSLETTER_RATE_LIMIT_PER_MINUTE` | `5` | per IP and per instance |
| `EVENT_HASH_SECRET` | empty | key used to hash IP addresses in the usage log; required in `prod` |
| `EVENT_RETENTION_DAYS` | `730` | default age for `POST /v1/admin/usage/purge` |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `10` | sign-up, login, reset and verification attempts, per IP and per e-mail |
| `TRUST_X_FORWARDED_FOR` | `false` | `true` only behind the ALB (uses the rightmost entry) |
| `WEB_API_SECRET` | empty | secret shared with the `energy-web` server: it trusts `X-Client-IP` and is required to send usage events; required in `prod` |

## Migrations

The schema is defined in `app/models.py` and versioned in `migrations/` (Alembic). No other
repository creates or alters tables.

- New migration: change `app/models.py`, run
  `uv run alembic revision --autogenerate -m "description"` and review the generated file.
- Apply: `uv run alembic upgrade head` (uses `DATABASE_URL`).
- In the full local environment, the `migrate` service of the `energy-data`
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

- **person (admin)**: the `admin` group in `cognito:groups`. `energy-web` handles the login
  (OAuth with PKCE) and calls the API from the server with the user's token;
- **machine (`energy-data`)**: the `energia-api/admin` scope, obtained through *client
  credentials* on the M2M app client.

Missing or invalid token: 401. Valid token without the group or the scope: 403.

## Member accounts

Free access to the portal is behind a sign-up (`/v1/auth/*`). The sign-up form collects a profile
that doubles as a lead record: role, company or institution, segment, company size, regions and
plants of interest, goals, optional phone and referral, and two separate consents (privacy
policy, required; contact, optional). `score_lead` (`app/leads.py`) turns it into a score and a
`hot`/`warm`/`cold` tier, listed by `GET /v1/admin/leads` (admin token).

| Route | Purpose |
|---|---|
| `POST /v1/auth/signup` | creates an unverified member and e-mails a 6-digit code (30 min). Always answers 202, so it does not reveal whether an address has an account. With `ENVIRONMENT=local` and the console e-mail backend the response also carries `dev_code`, so the flow can be tried without a mail provider |
| `POST /v1/auth/verify-email` | `{email, code}`; a wrong code counts an attempt and the code is locked after 5, so a new one is needed |
| `POST /v1/auth/resend-verification` | new code, replacing the previous one |
| `POST /v1/auth/login` | returns a 14-day opaque session token (stored hashed); 403 until the e-mail is verified |
| `GET /v1/auth/me`, `POST /v1/auth/logout` | `Authorization: Bearer <session token>` |
| `POST /v1/auth/forgot-password`, `POST /v1/auth/reset-password` | 1-hour link; a reset revokes every session |
| `POST /v1/auth/me/delete` | deletes the account after confirming the password (LGPD) |

Passwords use scrypt with a per-user salt; tokens are random and only their SHA-256 is stored.
`energy-web` keeps the session token in an `httpOnly` cookie and gates the pages. The data routes
stay public, so the gate is about lead capture, not about protecting the data.

## Usage log

Every page, filter, search, comparison and sign-in step is recorded in `usage_event`, so the free
phase can be measured. `energy-web` sends the events (`POST /v1/events`, protected by
`WEB_API_SECRET`, resolves the member from the session token); the API writes the auth,
newsletter and admin events itself.

| Field | Meaning |
|---|---|
| `category` | `page`, `interaction`, `auth`, `newsletter`, `admin` |
| `action` | `page.view`, `page.gated`, `page.engagement`, `chart.kind_change`, `compare.configure`, `search.plants`, `auth.signup`, `auth.login`, `auth.login_failed`, `newsletter.subscribe`, `admin.report_saved`, ... |
| `section`, `entity_type`, `entity_id` | derived from the path by `app/events.py`: `estados` + `state` + `BA`, `usinas` + `plant` + slug, `comparar` + `comparison` + the chosen items |
| `properties` | the filters in the URL (`de`, `ate`, `fonte`, ...) and the details of the interaction |
| `member_id`, `member_role`, `member_segment` | who did it, with the role and segment at that moment |
| `visitor_id`, `session_key` | first-party cookies, so the steps before sign-up can be linked |
| `ip_hash`, `user_agent`, `device`, `is_bot`, `referrer` | IP only as a keyed hash; bots are flagged and left out of the statistics |

Only an allow-list of query parameters is stored, so tokens in links never reach the log. Deleting
an account unlinks its events (`member_id`, visitor, IP hash and user agent are cleared).

Admin routes (Cognito admin token): `GET /v1/admin/usage/summary?days=30` (totals, daily series,
sections, top states/plants/reports, searches, compared items, interactions, sign-up funnel,
roles, active hot and warm leads), `GET /v1/admin/usage/events` (filters: `days`, `action`,
`section`, `member_id`), `GET /v1/admin/usage/events.csv` (raw export), `GET
/v1/admin/usage/members/{id}` (one person's timeline) and `POST /v1/admin/usage/purge`.

### Sending e-mail before SES

`EMAIL_BACKEND=console` only logs the message (the verification link shows up in the API log), which
is enough for local work. To send real messages without AWS, use any SMTP relay with
`EMAIL_BACKEND=smtp`:

- **Brevo** (free tier, about 300 e-mails a day): create an SMTP key, set `SMTP_HOST=smtp-relay.brevo.com`,
  `SMTP_PORT=587`, `SMTP_USERNAME` and `SMTP_PASSWORD` from the SMTP page, and verify the sender
  in `EMAIL_SENDER`.
- **Gmail** (small tests): turn on 2-step verification, create an app password,
  `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, your address as `SMTP_USERNAME` and the app
  password as `SMTP_PASSWORD`, and use the same address in `EMAIL_SENDER`.
- **Mailtrap** sandbox: captures the messages in a web inbox without delivering them.

Free limits change, so check the provider. Switch to `EMAIL_BACKEND=ses` when SES is ready; no code
changes.

## Revalidation (data → API → web)

1. After each load, `energy-data` calls `POST /v1/admin/revalidate` with the machine token and
   `{"tags": ["curtailment", "plants"]}`.
2. The API clears its in-memory cache and calls `POST {WEB_URL}/api/revalidate` with the
   `x-revalidate-secret: {REVALIDATE_SECRET}` header and the same tags.
3. `energy-web` calls `revalidateTag` for each tag.

The response says whether the web was reached (`web_revalidated`, `detail`); a web failure does
not change the 200 status. Saving a report through `POST /v1/admin/reports` also clears the API
cache.

## Pipeline and deployment

`.github/workflows/ci.yml` runs on every push (any branch), on every pull request and on demand
(*Actions → pipeline → Run workflow*). Jobs run in parallel:

| Job | What it checks |
|---|---|
| Lint | `ruff check` (includes the bandit `S` security rules) and `ruff format --check` |
| Type check | `mypy` (strict) |
| Tests | `alembic upgrade head` and `pytest` against a Postgres 16 service (migration round trip and model drift included), plus the `openapi.json` export |
| Dependency audit | `pip-audit` over the locked runtime dependencies (PyPA advisory database) |
| Secrets and misconfiguration scan | Trivy over the repository: leaked secrets and Dockerfile misconfigurations (HIGH/CRITICAL) |
| Build and scan image | Builds the Docker image and scans it with Trivy (OS and Python packages, HIGH/CRITICAL with a fix available) |

Any HIGH or CRITICAL finding fails the pipeline.

On `main` (push or manual run), once the AWS variables exist, the image job also pushes the
scanned image to ECR tagged with the commit SHA, and a **Deploy to production** job waits for
approval in the `production` environment: GitHub shows a *Review deployments* button on the run.
After approval it points `latest` at that SHA, runs the migration task
(`alembic upgrade head`), fails if the migration exits non-zero, and rolls the ECS service out
with `--force-new-deployment`, waiting until it is stable. To roll back, re-run the deploy job of
an older successful run.

Required repository settings (values come from the `energy-infra` outputs):

| Kind | Name |
|---|---|
| Secret | `AWS_ROLE_ARN` |
| Variables | `AWS_REGION`, `ECR_REPOSITORY`, `ECS_CLUSTER`, `ECS_SERVICE`, `ECS_MIGRATION_TASK_DEFINITION`, `ECS_SUBNETS`, `ECS_SECURITY_GROUP` |
| Environment | `production`, with required reviewers |

Without `ECS_CLUSTER`, the image is built and scanned but not pushed, and the deploy job is
skipped.

`.github/dependabot.yml` opens weekly update PRs for the Python dependencies, the Docker base
image and the GitHub Actions, with a 14-day cooldown (security updates are not delayed).

Other notes:

- `Dockerfile`: `python:3.12-slim` image with the Debian security updates applied, dependencies
  installed by `uv sync --frozen --no-dev`, unprivileged user, `uvicorn` on port 8000. The same
  image runs the migrations (`alembic upgrade head`).
- `[tool.uv] exclude-newer` keeps dependencies at least two weeks old;
  `exclude-newer-package` lifts that only for packages that need a security fix (currently
  `certifi`, GHSA-6q48-6c7w-9gw2).
- The cache and the newsletter limit live in memory, per instance; with several tasks, each one
  has its own.
