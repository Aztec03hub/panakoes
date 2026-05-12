# infra

Terraform configurations for Panakoes infrastructure.

## Layout

- bootstrap/  One-time setup creating remote state backend (S3 + KMS
              + DynamoDB lock). Apply this first, before anything else.
- global/     Account-wide, region-agnostic resources: GitHub Actions
              OIDC identity provider and the IAM role workflows assume.
              Uses the S3 backend from `bootstrap/`.
- global/cloudflare-dns/  Cloudflare-managed DNS records for the two
              LaFayette Labs zones (`panakoes.com`, `lafayettelabs.com`).
              Brings the previously-manual dashboard config under
              Terraform: SES verification + DKIM, Cloudflare Pages
              apex + www, DMARC, SPF, and Cloudflare Email Routing
              MX. Uses the `cloudflare/cloudflare ~> 4.0` provider;
              API token is operator-local (`TF_VAR_cloudflare_api_token`),
              never committed. Existing manually-added records are
              brought into state via `terraform import` per the
              module README. State key:
              `global/cloudflare-dns/terraform.tfstate` in the
              bootstrap S3 backend.
- dev/network/  Per-environment networking primitives for the dev
              environment (VPC, subnets, NAT, IGW, route tables, flow
              logs). First config that consumes the bootstrap-created
              S3 backend.
- dev/data/   Per-environment DynamoDB tables for the dev environment
              (ingestion records, audit log, streaming session state).
              All tables PAY_PER_REQUEST, SSE enabled, point-in-time
              recovery, deletion protection. The audit-log table also
              carries a Tier3ActionIndex GSI so admin-api can back the
              Tier 3.3 audit-log read view.
- dev/admin-state/  Per-environment DynamoDB tables that back the
              admin dashboard's Tier 2 (cost and budget tracker) and
              Tier 3 (secure lifecycle controls) features: cost-cache
              (Cost Explorer cache), tenant-cost-rollup (per-tenant
              daily aggregates), lifecycle-state (Tier 3 idempotency
              + result envelope), alert-state (anomaly dedup). Lives
              outside dev/data/ so Tier 3 schema changes have their
              own apply boundary and cannot accidentally impact the
              ingestion / audit / streaming-sessions tables.
- dev/storage/  Per-environment S3 buckets for the dev environment:
              audio uploads (client uploads via the Ingestion API),
              transcripts (transcription pipeline output), and the
              long-term log archive. Each bucket has a dedicated CMK,
              versioning, public-access blocked, TLS-only policy, and
              tier-and-expiry lifecycle rules.
- dev/secrets/  Per-environment AWS Secrets Manager secrets for the
              dev environment microservices: JWT signing key,
              Anthropic API key, Stripe test + webhook secrets,
              Postgres password, database URL, and SES SMTP
              credentials. All secrets KMS-encrypted with a
              dedicated dev CMK, created with placeholder values
              (real values written post-apply via the AWS CLI).
- dev/iam/    Per-environment least-privilege IAM roles for every
              Panakoes microservice. Provisions a task role per
              service (runtime identity for application code) and
              an ECS task execution role per ECS service (image pull
              + log shipping + startup-secret injection). Also
              creates the GPU instance role + instance profile that
              gpu-spawner passes to launched EC2 instances. Every
              policy uses explicit Resource ARNs and condition keys
              wherever the AWS API allows.
- dev/ecr/    Per-environment ECR repositories for the dev environment,
              one per Panakoes microservice (11 repos: auth, billing,
              event-router, gpu-spawner, ingestion-api, notification,
              query-api, session-manager, summarization,
              transcriber-batch, transcriber-stream). All repositories
              use `IMMUTABLE` tag mutability, scan-on-push, a single
              shared customer-managed KMS key, and a lifecycle policy
              that keeps the last 10 tagged images and expires
              untagged images after 14 days.
- dev/waf/    Per-environment regional WAFv2 web ACL fronting the
              public-facing Panakoes APIs (Ingestion, Query, Auth)
              once their ALBs / API Gateways exist. ACL composes
              four AWS Managed Rule Groups (Common, Known Bad
              Inputs, IP Reputation, SQL Database), a 1000-req /
              5-min per-IP rate limit (with `/health` exempt via
              scope-down), and a commented geo-block placeholder.
              Logs flow to a dedicated KMS-encrypted CloudWatch log
              group with `Authorization` and `Cookie` headers
              redacted.
- dev/observability/  Per-environment CloudWatch observability
              primitives for the dev environment: dedicated KMS CMK
              `alias/panakoes-dev-logs`, one CloudWatch Log Group per
              service at `/panakoes/dev/<service>` (30-day retention,
              KMS-encrypted), per-service error-count metric filters,
              and a long-term S3 log archive bucket with multi-tier
              lifecycle (STANDARD to IA at 30d, GLACIER_IR at 90d,
              DEEP_ARCHIVE at 365d). Subscription-filter wiring
              (Firehose vs Lambda forwarder) deferred to a follow-up.
- dev/step-functions/  Per-environment Step Functions state machine
              that orchestrates the long-audio transcription
              pipeline. STANDARD-type workflow with a Choice between
              a single-Batch-job short path (audio under 10 minutes)
              and a chunk-and-fan-out long path (parallel Map over
              8-minute overlapping chunks, MaxConcurrency 8). Every
              Task carries an exponential-backoff retry policy and a
              Catch handler that routes to a NotifyFailure Lambda.
              KMS-encrypted CloudWatch log group at
              `/aws/states/panakoes-dev-long-audio`, level=ALL with
              execution data included. Bypasses Lambda's 15-minute
              hard ceiling, which is the architectural reason this
              module exists.
- dev/security/  Per-environment security observability stack for
              the dev environment: AWS Config (recorder + delivery
              channel + three free-tier managed rules), Amazon
              GuardDuty detector, and AWS Security Hub (account +
              AWS Foundational Security Best Practices + CIS AWS
              Foundations standards). Plan-clean by default. Each
              paid service gates behind a `bool` variable
              (`enable_config`, `enable_guardduty`,
              `enable_security_hub`, all defaulting to false) so a
              default `terraform apply` provisions the supporting
              infrastructure (KMS CMK `alias/panakoes-dev-security`,
              S3 delivery bucket with TLS-only policy and tier-to-IA
              at 90d / expire at 1y lifecycle, IAM service role with
              `AWS_ConfigRole` managed policy, idle GuardDuty
              detector) without starting any per-event billing.
              Flipping each variable to true is a deliberate
              post-apply step.
- dev/auth-db/  Per-environment Aurora Serverless v2 PostgreSQL 16
              cluster backing the Better-Auth tables (`user`,
              `session`, `account`, `verification`) for the auth
              microservice. Cluster sits in the three private
              subnets from `dev/network/` via a dedicated DB subnet
              group, scales between 0.5 and 4 ACUs, and is reachable
              on 5432/TCP from inside the dev VPC CIDR. Storage and
              Performance Insights encrypted with a dedicated CMK
              (`alias/panakoes-dev-auth-db`, rotation enabled,
              7-day deletion window). 7-day backup retention,
              deletion protection on, `skip_final_snapshot = true`
              for dev. Master password resolves from
              `panakoes-dev/postgres-auth-db-password` via
              `terraform_remote_state` with a `try()` fallback to a
              Terraform-managed `random_password` so plans run
              clean before secrets is applied; rotated post-apply
              via `aws rds modify-db-cluster`. RDS Enhanced
              Monitoring role (60s granularity) and Performance
              Insights (free tier, 7-day retention) provisioned for
              the writer instance.
- dev/events/   Per-environment async messaging backbone for the dev
              environment: a custom EventBridge bus (`panakoes-dev`),
              three pipeline-stage rules (audio uploaded, transcript
              completed, summary completed), four SQS queues with
              matching DLQs, three SNS fan-out topics (system alerts,
              billing events, user notifications), and CloudWatch
              alarms on every DLQ. Single shared CMK encrypts all
              SNS topics and SQS queues.
- dev/backup/ Per-environment AWS Backup vault and plan for the dev
              environment's stateful resources. Single
              KMS-encrypted vault, daily plan with 30-day retention
              and monthly plan with 365-day retention, IAM service
              role with the AWS-managed backup + restore policies,
              and SNS notifications on key vault events. Selection
              protects the three DynamoDB tables in `dev/data/` by
              ARN today and by `Backup = enabled` tag going
              forward.
- dev/batch/  Per-environment AWS Batch GPU compute environment, job
              queue, and job definition for the async transcription
              pipeline (Whisper-large-v3 fp16 on g4dn.xlarge Spot).
              Compute environment is `MANAGED` with allocation
              `SPOT_CAPACITY_OPTIMIZED`, scales 0/0/16 vCPUs (pure
              pay-per-use), and runs in the dev VPC's private
              subnets. Job definition pins 4 vCPU / 15000 MiB /
              1 GPU and uses the `transcriber-batch` task role from
              `dev/iam/`. Owns the project-wide `system-alerts` SNS
              topic and a CloudWatch alarm on `FailedJobs > 0` over
              5 minutes. GPU AMI is a placeholder until the Packer
              build module ships.
- dev/api-gateway/  Per-environment public ingress for every
              Panakoes microservice. Provisions an
              `aws_apigatewayv2_api` (HTTP API named
              `panakoes-dev-public`) with CORS for the production
              marketing domain, the LaFayette Labs site, and the
              local Vite dev server; a shared `aws_apigatewayv2_vpc_link`
              spanning all three private subnets; one
              `aws_apigatewayv2_integration` per upstream service
              (auth, ingestion-api, summarization, notification,
              query-api, session-manager, billing) wired to
              placeholder NLB listener ARNs (real NLBs land with
              ECS); 25 routes plus a public `GET /health` MOCK
              integration; an auto-deploy `dev` stage with
              throttling burst 5000 / rate 10000 and structured-JSON
              access logs to a KMS-encrypted CloudWatch log group;
              optional WAF web ACL association via `try()` against
              `dev/waf`; and three CloudWatch alarms (4xx > 10% over
              10 min, 5xx > 1% over 5 min, integration latency p99
              > 2 s). Custom domain (`api.panakoes.com`) is left as
              a commented-out skeleton until DNS and ACM are wired.
- dev/frontend/ Per-environment static-asset hosting tier for the
              SvelteKit admin app: a private S3 origin bucket
              (`panakoes-dev-frontend-<suffix>`, CMK-encrypted with
              alias `alias/panakoes-dev-frontend`, versioning,
              public access blocked) accessed exclusively via a
              CloudFront Origin Access Control (OAC); a CloudFront
              distribution `panakoes-dev-admin` using the AWS
              managed Caching-Optimized cache policy and the
              Managed-SecurityHeadersPolicy response-headers policy;
              SPA fallback (403 + 404 -> /index.html) for client-side
              routing; and a separate access-log bucket
              (`panakoes-dev-frontend-logs-<suffix>`) with 90-day
              lifecycle. Price class PriceClass_100 (US/Europe).
              WAF association is pre-wired but disabled because the
              dev/waf ACL is REGIONAL-scoped; flip when a
              CloudFront-scoped ACL exists.
- dev/vpc-endpoints/  Per-environment VPC endpoints for the dev
              environment. Two free gateway endpoints (S3,
              DynamoDB) attach to all private route tables. Ten
              paid interface endpoints (secretsmanager, ssm, kms,
              ecr.api, ecr.dkr, logs, events, sns, sqs, sts) attach
              to all three private subnets with private DNS
              enabled. A shared security group fronts the
              interface endpoints, allowing 443/TCP from the VPC
              CIDR. Routes AWS API traffic off the NAT to cut
              egress costs and keeps service traffic inside the
              VPC.
- dev/ecs/    Per-environment ECS Fargate cluster (`panakoes-dev`)
              plus the first application service deploy: the auth
              microservice. Provisions the cluster (Container
              Insights enabled, `FARGATE` + `FARGATE_SPOT` capacity
              providers), the internal NLB + TCP listener + IP
              target group fronting auth, the auth task SG (ingress
              from the API Gateway VPC Link SG only), the Fargate
              task definition (ARM64 / Graviton, 256 CPU / 512 MiB,
              env vars + secrets pulled from `dev/secrets/`), and
              the ECS service (1 task desired in dev, deployment
              circuit breaker on, `lifecycle.ignore_changes` on
              `task_definition` + `desired_count` so out-of-band
              CD deploys do not roll back). Exposes the contract
              output `nlb_listener_arns` (map of service name to
              listener ARN) that `dev/api-gateway/` consumes via
              `terraform_remote_state` when its
              `discover_ecs_nlbs` flag is true. Pattern-setting
              module for the remaining six application services
              (ingestion-api, summarization, notification,
              query-api, session-manager, billing); see
              `infra/dev/ecs/README.md` for the add-a-service
              recipe.
- dev/ses/    Per-environment Amazon SES bootstrap for the dev
              environment: DKIM-verified domain identity
              (`lafayettelabs.com`), sandbox-mode email-identity
              (`phil@lafayettelabs.com`), configuration set
              (`panakoes-dev`) with CloudWatch event publishing
              (SEND, DELIVERY, BOUNCE, COMPLAINT, OPEN, CLICK,
              RENDERING_FAILURE, DELIVERY_DELAY, REJECT, SUBSCRIPTION),
              and a dedicated IAM user whose access key is converted
              offline to SMTP credentials populated into
              `panakoes-dev/ses-smtp-credentials`. IAM policy is
              scoped to the two identity ARNs plus the configuration
              set ARN with a `ses:FromAddress` condition; no
              wildcards. Sandbox-mode by default; production exit
              via the support-case checklist in
              `docs/runbooks/ses-bootstrap.md`.
- dev/budgets/ Per-environment AWS Budgets cost-guardrail stack: one
              account-wide monthly budget ($100/mo) with four
              thresholds (50/80/100% ACTUAL + 80% FORECASTED), four
              service-specific budgets (EC2 $35, Aurora $15, Bedrock
              $25, CloudFront + S3 $5), and one tag-scoped budget
              filtered on `Project=panakoes` ($100/mo) for future
              multi-environment rollups. All notifications fan out
              through a shared SNS topic (`panakoes-dev-budget-alerts`)
              with an email subscription on `phil@lafayettelabs.com`
              and a `dev-budget-100pct-actual` CloudWatch alarm on the
              topic's `NumberOfMessagesPublished` metric. SNS topic
              policy scopes Publish to `budgets.amazonaws.com` with
              `aws:SourceAccount` + `aws:SourceArn` conditions per the
              AWS service-confused-deputy guidance. Pairs with
              `dev/cost-anomaly-monitor` (statistical anomalies on top
              of historical baseline) for full cost-guardrail
              coverage.
- (TBD)       Additional per-environment configurations (staging,
              prod, RDS, Lambda) land here in subsequent commits.

## AMIs

Custom Amazon Machine Images built with Packer rather than Terraform.
Packer is the right tool here: it bakes a long-lived artifact (AMI ID)
that downstream Terraform consumes. Mixing both into one Terraform
config would couple `terraform apply` cadence to AMI rebuilds, which is
the opposite of what we want.

- ami/gpu-transcribe/  Packer template for the GPU AMI used by both
              transcription paths. Source = latest AWS Deep Learning AMI
              GPU on Ubuntu 22.04 (NVIDIA drivers + CUDA + Docker
              pre-installed). Provisioners pre-bake Whisper-large-v3
              fp16 weights, faster-whisper-large CT2 weights, Silero VAD
              weights, and the panakoes-dev-transcriber-stream container
              image. Build host = g4dn.xlarge to mirror runtime
              hardware; bake cost ~$0.20-0.50 per build. Output AMI is
              tagged `Project=panakoes`, `Environment=<env>`,
              `AmiPurpose=gpu-transcribe`, `BakedAt=<UTC timestamp>`.
              See `infra/ami/gpu-transcribe/README.md` for the full
              build + rotation procedure.

## Standard workflow

1. Apply the bootstrap module once per AWS account.
2. Reference the bootstrap outputs in each environment's
   `providers.tf` `backend "s3"` block.
3. Subsequent configurations use the S3 backend with state locking
   via DynamoDB.

## Conventions

- All resources tagged: Project, Environment, ManagedBy, Module.
- All S3 buckets have public access blocked, versioning enabled,
  KMS-encrypted.
- All DynamoDB tables use PAY_PER_REQUEST billing.
- KMS keys have rotation enabled and 30-day deletion window.

## CI: per-module plan on every PR

Every PR that touches `infra/**` runs the `Terraform plan on PR`
workflow (`.github/workflows/terraform-plan-on-pr.yml`). The workflow:

1. Diffs `origin/main...HEAD` to find which `infra/dev/<module>`
   directories were touched.
2. Fans out a matrix job per changed module.
3. Assumes the `panakoes-github-actions` AWS role via OIDC (same role
   used by `terraform-ci.yml`; no long-lived keys), then runs
   `terraform init` and `terraform plan -lock-timeout=2m -out=tfplan.bin`
   in that module's directory.
4. Posts (or updates) a sticky PR comment per module via
   `marocchino/sticky-pull-request-comment@v2` with the first 200 lines
   of the plan inside a collapsible `<details>` block.
5. Uploads the full plan as a workflow artifact named
   `terraform-plan-<module>` retained for 14 days.

This workflow **never applies**. It is plan + comment + label-gated
fail only.

### Sticky comment shape

Each module gets its own sticky comment, distinguished by header
`terraform-plan-<module>`. Re-pushing a branch updates the same
comment instead of stacking new ones, so the comment thread stays
focused on the most recent plan rather than a chronological log.

The comment carries:

- A heading: `Terraform plan for infra/dev/<module>`.
- A collapsed `<details>` block holding the truncated plan text.
- The total line count (so reviewers know how much was clipped).
- A pointer to the workflow-artifact name for the full plan.

### Downloading the full plan artifact

When the sticky comment truncates the plan, the full text is attached
to the workflow run as `terraform-plan-<module>`. To fetch it:

- From the GitHub UI: open the PR's Checks tab, click the
  `Terraform plan on PR` run, scroll to the `Artifacts` section at
  the bottom, and download the matching artifact.
- From the CLI: `gh run download <run-id> -n terraform-plan-<module>`.

Artifacts expire after 14 days.

### The `replace-allowed` label

By default the workflow **fails** any plan that would destroy or
replace one or more resources. This is intentional friction: a plain
`terraform plan` summary line ("1 to destroy") is easy to miss in a
review, and a replace on a stateful resource (RDS, DynamoDB,
Secrets Manager) can silently nuke data.

To bypass the gate, add the `replace-allowed` label to the PR. The
gate re-runs on the next push or label update and passes once the
label is present. Removing the label (or leaving it off) restores the
gate immediately.

The failure message reviewers see when the gate trips is:

> Plan for `infra/dev/<module>` would destroy or replace resources.
> Add the `replace-allowed` label to this PR to acknowledge and
> proceed, or revise the change so no resources are destroyed or
> replaced. See `infra/README.md` for the replace-allowed workflow.

Recommended review etiquette before applying the label:

1. Pull the full plan artifact (sticky comment usually truncates).
2. Identify every resource marked `will be destroyed` or
   `must be replaced` and confirm each is intended.
3. For stateful resources, confirm the data is backed up or
   reproducible.
4. Apply the label and re-trigger CI (push an empty commit, or wait
   for the workflow's `labeled` event to re-run automatically on the
   next push).
