# 3. Idempotent monthly loads with a rolling three-month reprocessing window

- **Status:** Accepted
- **Scope:** energy-data

## Context

ONS revises data it has already published, sometimes weeks later. A pipeline that loads each
month once would keep the first, wrong version forever. Re-running everything every time is
wasteful and slow, and a load that fails halfway must not leave a month half-written for the
portal to read.

## Decision

- The pipeline runs on the 3rd of every month and **reprocesses the last three months**
  (`REPROCESS_MONTHS`).
- `extract` skips the download when the file's **ETag** has not changed, and stores the raw
  file in `bronze/` with its sha256, URL and ETag.
- Data moves through **bronze → silver → gold**; invalid rows go to `quarantine/` instead of
  failing the run.
- **Quality gates** run before publishing; any ERROR-level check blocks the load.
- `publish` is idempotent: it **deletes and reinserts the month in a single transaction**, so
  readers see either the old month or the new one, never a mix.
- Every run, successful or not, is recorded in `load_run` and shown in the admin area.

## Consequences

- Revisions from ONS are picked up automatically within three months, with no manual backfill.
- Re-running a month is always safe, which makes recovery from failures trivial.
- Unchanged files cost one HTTP request, so the extra reprocessing is cheap.
- Corrections older than three months still need a manual run (`--from` / `--to`), which is
  documented.
