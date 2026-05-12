# services/gpu-spawner

GPU Spawner microservice for Panakoes. Wraps `ec2:RunInstances` and
`ec2:TerminateInstances` to spawn a per-session g4dn.xlarge Spot
instance from the streaming-transcriber Packer AMI. The session-manager
calls this service over HTTP with a service-actor JWT to start, query,
or stop a session's GPU.

> **This service has compute-spawning authority; the IAM role's
> `ec2:RunInstances` is constrained by tag-on-create + instance-type +
> AMI. See `infra/dev/iam/README.md` for the full policy boundary.**

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| GET    | `/health`                   | no                       | Liveness probe |
| POST   | `/spawn`                    | service-actor JWT only   | Launch a Spot g4dn.xlarge for a session |
| GET    | `/spawn/{instance_id}`      | user or service          | Read instance state + tags (user-actor calls scoped to their `SessionId`) |
| DELETE | `/spawn/{instance_id}`      | service-actor JWT only   | Terminate after verifying the `Spawner` tag matches |

User-actor JWTs sent to `POST /spawn` or `DELETE /spawn/{id}` get HTTP
403. Tokens missing the `actor_type` claim, or with an unknown actor
type, get HTTP 401.

## Configuration

Read from environment variables (see
`src/panakoes_gpu_spawner/config.py`). There are NO hardcoded AMI ids,
subnet ids, or security group ids; missing values force a conscious
configuration decision at deploy time:

| Variable | Default | Notes |
| --- | --- | --- |
| `JWT_SECRET` | (dev placeholder) | Must match the Auth service's HS256 secret |
| `JWT_ISSUER` | `https://auth.panakoes.com` | Claim-validated |
| `JWT_AUDIENCE` | `panakoes-api` | Claim-validated |
| `GPU_AMI_ID` | (empty) | Required. AMI id from the Packer build (`infra/packer/streaming-transcriber/`) |
| `GPU_SECURITY_GROUP_ID` | (empty) | Required. Security group id from `infra/dev/network/` |
| `GPU_SUBNET_ID` | (empty) | Required. Private subnet id from `infra/dev/network/` |
| `GPU_INSTANCE_TYPE` | `g4dn.xlarge` | Constrained by IAM; do not override without updating policy |
| `GPU_IAM_INSTANCE_PROFILE` | `panakoes-dev-gpu-instance` | The instance profile the launched EC2 assumes |
| `PROJECT_TAG` | `panakoes` | Required tag value for IAM gating |
| `GPU_SPAWNER_TAG` | `panakoes-dev-gpu-spawner` | Required tag value for IAM gating |
| `SESSION_MANAGER_WS_ENDPOINT` | `wss://session-manager.panakoes.com` | Embedded into the user-data script |
| `AWS_REGION` | `us-east-1` | EC2 region |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |

## Authentication

All endpoints except `/health` require `Authorization: Bearer <jwt>`.
The token must be HS256-signed with `JWT_SECRET` and carry:

- `sub`: actor id (user id for user actors, service name for service actors)
- `actor_type`: `user` or `service`
- `jti`: session id (the streaming session this token is bound to)
- `iss`, `aud`, `iat`, `exp`: standard claims, all validated

Compute-spawning routes (`POST /spawn`, `DELETE /spawn/{id}`) require
`actor_type=service`. Read-only state lookup accepts both actor types;
user actors are scoped to instances whose `SessionId` tag matches their
`jti` claim, and the route returns 404 (not 403) when the scope check
fails so cross-session existence is not leaked.

## Tagging contract

Every launched instance carries four tags. The IAM policy gates
`RunInstances` and `TerminateInstances` on the first two; the latter
two carry session ownership for application-layer authorization:

- `Project = panakoes`
- `Spawner = panakoes-dev-gpu-spawner`
- `SessionId = <session_id from the request body>`
- `UserId = <user_id from the request body>`

A confused-deputy guard at the application layer refuses to terminate
or describe instances whose `Spawner` tag does not match this service's
identity, even if the IAM policy were ever loosened in error.

## Running locally

```bash
uv sync --group dev
uv run uvicorn panakoes_gpu_spawner.main:app --reload
```

## Running tests

```bash
uv run pytest
```

Tests use `moto`'s `@mock_aws` decorator to stand up an in-memory EC2.
moto's `RunInstances` covers the basic launch flow we exercise, but it
does NOT enforce the IAM tag-on-create conditions, instance-profile
existence, or spot-pricing semantics that real AWS would. **Integration
tests against a real AWS account (which validate the IAM policy
boundary end to end) are deferred** to a follow-up; the policy itself
is exercised today by `terraform validate` and by the unit tests on
the EC2 wrapper.

## Linting and type checking

```bash
uv run ruff check
uv run mypy src
```

## Building the Docker image

Canonical bake path is GitHub Actions (`.github/workflows/image-bake-on-change.yml` on push to `main`, or the `image-bake-manual.yml` one-button workflow). The local command below is a fallback for offline dev.

```bash
docker build -t panakoes-gpu-spawner .
```
