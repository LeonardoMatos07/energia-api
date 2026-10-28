# 6. Keyless deploys with GitHub OIDC and gated production environments

- **Status:** Accepted
- **Scope:** spillway-infra, spillway-api, spillway-data, spillway-web

## Context

CI needs to push images, run migrations, deploy services and apply Terraform. Long-lived AWS
access keys stored as GitHub secrets leak, are rarely rotated, and grant the same power to
every branch and pull request.

## Decision

- GitHub Actions authenticates to AWS through **OIDC**; there are no static AWS keys anywhere.
- Each repository gets its own role, trusted only for specific subjects:
  - `spillway-api` and `spillway-data`: pushes to `main` and the `production` environment;
  - `spillway-infra`: a **read-only plan role** for pull requests and `main`, and a separate
    **apply role** assumable only from the `production` environment.
- Every production deploy and every `terraform apply` runs in the GitHub `production`
  environment, which requires a reviewer's approval.
- The portal deploys through Vercel; its CI runs lint, types and tests.

## Consequences

- No AWS credential to rotate or leak; a compromised pull request cannot deploy.
- Merging to `main` builds and tests automatically, but nothing reaches production without a
  human approval.
- The apply role is broad (`AdministratorAccess`) because Terraform creates IAM roles; the
  approval gate, not the policy, is the control. Narrowing the policy is possible but needs
  constant upkeep.
- Renaming a repository changes its OIDC subject; the trust policy must be applied before the
  rename, or deploys from the renamed repository are rejected.
