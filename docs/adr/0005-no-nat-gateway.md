# 5. No NAT gateway: public subnets with no inbound access, private database

- **Status:** Accepted
- **Scope:** energy-infra

## Context

The standard AWS layout puts compute in private subnets behind a NAT gateway. For a small
product, the NAT's fixed hourly fee plus traffic costs more than the whole API service
(the estimate for the full stack is about US$170–270 per month; a NAT would add roughly 40
more). The workloads still need outbound internet access: ECR, ONS open data, Cognito, SES.

## Decision

- Fargate tasks and the Airflow EC2 instance run in **public subnets with a public IP but no
  open inbound ports**: the tasks accept traffic only from the ALB security group, and the EC2
  instance accepts nothing. Operators reach the instance through **SSM Session Manager**, with
  no SSH.
- **RDS stays in private subnets** with no route to the internet.
- S3 is reached through a **gateway endpoint**.
- The ALB accepts only CloudFront's origin IPs (managed prefix list) and requests carrying a
  secret `X-Origin-Verify` header, so the CDN and its cache cannot be bypassed.

## Consequences

- A meaningful share of the monthly bill is saved with no loss of inbound isolation.
- Security relies on security groups rather than on network topology; they are defined in
  Terraform and scanned in CI.
- Each public IPv4 is billed by the hour, which the cost estimate accounts for.
- If traffic or compliance requirements grow, adding a NAT and moving compute to private
  subnets is a contained Terraform change.
