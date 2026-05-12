"""Panakoes WebSocket $connect Lambda authorizer.

Public surface:

- `lambda_handler`: the AWS Lambda entrypoint invoked by API Gateway v2
  on every $connect handshake.
- `extract_token`: pure helper that pulls the JWT out of an
  API Gateway v2 authorizer event.
- `build_response`: pure helper that constructs the authorizer
  response shape API Gateway expects.

The Auth + Reject paths are intentionally narrow and pure (no global
state, no IO beyond the validator) so 100% branch coverage is
achievable from unit tests alone. boto3 / secrets-manager IO happens
at Lambda boot via env-var injection, not in the handler hot path.
"""

from panakoes_ws_authorizer.authorizer import (
    build_response,
    extract_token,
    lambda_handler,
)

__all__ = ["build_response", "extract_token", "lambda_handler"]
