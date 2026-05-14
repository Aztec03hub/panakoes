---
category: Added
---

- `docker-compose.yml`: extend LocalStack services to include `secretsmanager`, `kms`, `sts`, `iam`, and `logs` for full zero-cost integration test coverage.
- `scripts/localstack-init.sh`: new idempotent bootstrap script that creates all S3 buckets, SQS queues, DynamoDB tables, KMS keys, and Secrets Manager secrets that Panakoes services expect at startup.
- `scripts/dev-up.sh`: automatically runs `localstack-init.sh` after LocalStack becomes healthy when `DEV_LOCALSTACK=1`.
- `Makefile`: add `make test-local` target that starts the full local stack and runs the entire Python test suite with `AWS_ENDPOINT_URL` pointed at LocalStack.
- `conftest.py`: root-level pytest fixtures (`localstack_url`, `localstack_available`) shared across all services for integration-test gating.
