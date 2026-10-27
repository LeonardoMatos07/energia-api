# Architecture Decision Records

Decisions that shape Spillway across its four repositories. Each record states the context,
the decision and its consequences, including the trade-offs accepted.

| # | Decision | Scope |
|---|---|---|
| [0001](0001-four-repositories-with-a-written-contract.md) | Four repositories with a written contract; the API owns the schema | all |
| [0002](0002-allocate-official-curtailment-to-plants.md) | Allocate the official ONS curtailment to plants instead of using plant estimates | data, api, web |
| [0003](0003-idempotent-monthly-loads-with-rolling-reprocessing.md) | Idempotent monthly loads with a rolling three-month reprocessing window | data |
| [0004](0004-event-driven-cache-revalidation.md) | Event-driven cache revalidation from the pipeline to the portal | data, api, web |
| [0005](0005-no-nat-gateway.md) | No NAT gateway: public subnets with no inbound access, private database | infra |
| [0006](0006-keyless-deploys-with-github-oidc.md) | Keyless deploys with GitHub OIDC and gated production environments | all |

New records use the next number and the same sections (Status, Scope, Context, Decision,
Consequences). A superseded record stays in place with its status changed and a link to the
record that replaces it.
