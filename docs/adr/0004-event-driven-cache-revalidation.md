# 4. Event-driven cache revalidation from the pipeline to the portal

- **Status:** Accepted
- **Scope:** energy-data, energy-api, energy-web

## Context

The data changes once a month, but users expect to see a new month as soon as it is loaded.
Rendering every page on every request would put the API in the critical path of every visit;
rebuilding the whole site after each load couples the deploy to the data. A short time-based
cache alone either serves stale data for too long or hits the API too often.

## Decision

- The portal uses Next.js **ISR** with tagged fetches (`curtailment`, `plants`, `reports`) and a
  one-hour time-based fallback. Dynamic pages render on first access, so the build does not
  depend on the API.
- After each successful load, the pipeline triggers a chain:
  1. `energy-data` calls `POST /v1/admin/revalidate` with a **Cognito machine token**
     (client credentials, `admin` scope).
  2. `energy-api` clears its own cache and calls the portal's `/api/revalidate` with a shared
     secret.
  3. `energy-web` compares the secret in **constant time** and calls `revalidateTag` for each
     tag.

## Consequences

- New data appears right after a load, while normal traffic is served from cache.
- If the API is down, cached pages keep working and recover on their own within the hour.
- Each hop has its own credential: the pipeline never holds the portal's secret, and the
  portal never accepts calls from the pipeline directly.
- Revalidation runs after `publish`, so a failure there never leaves a month half-written:
  the data is already in Postgres, the `notify_portal` task fails visibly, and the one-hour
  fallback refreshes the pages anyway.
