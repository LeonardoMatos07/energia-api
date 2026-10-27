# 1. Four repositories with a written contract; the API owns the schema

- **Status:** Accepted
- **Scope:** energy-api, energy-data, energy-web, energy-infra

## Context

Spillway has four parts with different runtimes, release cadences and deploy targets: an
Airflow pipeline (EC2), a FastAPI service (ECS Fargate), a Next.js portal (Vercel) and the AWS
infrastructure (Terraform). They share one Postgres database.

Two writers of a schema is the usual way shared databases rot: the pipeline adds a column the
API does not know about, or a migration in one place breaks a query in the other.

## Decision

- One repository per part: `energy-data`, `energy-api`, `energy-web`, `energy-infra`.
- The boundaries are written down in [`phase1-contract.md`](../phase1-contract.md): routes,
  payloads, tables, columns, environment variables and who calls whom. A change to a shared
  surface starts in the contract.
- `energy-api` is the **only owner of the Postgres schema** (Alembic). `energy-data` only
  writes rows into tables that already exist; before each load it checks for them and fails with
  "run the energy-api migrations" if they are missing. Locally, the `migrate` service in the
  `energy-data` Compose file builds `energy-api` and runs `alembic upgrade head`.
- `energy-web` never hand-writes API types: they are generated from the API's OpenAPI document
  (`scripts/gen-api.mjs`).

## Consequences

- Each repository has its own CI and deploy, and can ship without waiting for the others.
- Schema drift is caught before a load, not as a wrong number on the portal.
- A cross-cutting change touches up to three repositories and the contract; that friction is
  deliberate, because it is the moment to agree on the interface.
- Local development needs the repositories side by side (`../energy-api`), which the READMEs
  document.
