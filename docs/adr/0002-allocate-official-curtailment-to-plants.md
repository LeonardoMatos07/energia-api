# 2. Allocate the official ONS curtailment to plants instead of using plant estimates

- **Status:** Accepted
- **Scope:** energy-data (modelling), energy-api and energy-web (what is shown)

## Context

The product answers "how much energy did *this plant* lose, why, and what was it worth". ONS
publishes curtailment at two levels and neither answers that alone:

- **Cluster level:** the official *Geração Não Realizada Apurada* (GNRa) and the restricted
  minutes per reason in each half hour, but not per plant.
- **Plant level:** estimated and verified generation and a restricted flag, but neither the
  official curtailment nor the reason.

The obvious shortcut, summing `estimated − verified` per plant, overstates the official figure,
and not by a constant factor: 1.32× the GNRa in Jan/2023, 1.10× in Jan/2025, 1.02× in
Sep/2026. A constant correction would still be wrong month to month.

## Decision

1. **The official number wins.** The cluster GNRa (average MW × 0.5 h) is the total.
2. **Split by reason** in proportion to the restricted minutes: `REL` (transmission / external
   unavailability), `CNF` (reliability), `ENE` (more supply than the grid absorbs) and `other`.
3. **Allocate to plants** in proportion to each plant's estimated loss in the restricted half
   hours; fall back to estimated generation, then to equal parts.
4. **Value** each curtailed half hour at the subsystem CMO, bounded by the yearly PLD floor and
   ceiling.
5. A quality gate checks that the plant totals add back up to the official figure (1%
   tolerance) and blocks the load otherwise.

## Consequences

- Plant figures always reconcile with what ONS publishes, so any number on the portal can be
  traced to an official source; this matters for a product whose users are generators and
  traders.
- Per-plant values are an allocation, not a measurement. The methodology page says so
  explicitly.
- The edge cases found in the raw data (plants without a cluster, plants joining a cluster
  mid-month, missing days of CMO) each need an explicit rule; they are listed in the
  `energy-data` README.
