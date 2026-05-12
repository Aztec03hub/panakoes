"""Panakoes streaming-router Lambda.

Routes every WebSocket frame from `panakoes-dev-streaming-ws` to the
appropriate side-effect (DynamoDB session row, SQS audio fan-out,
EventBridge gpu-spawner trigger).

Public surface:

- `lambda_handler`: the AWS Lambda entrypoint.
- `Router`: a class encapsulating the dispatch logic. Tests construct
  it directly with explicit AWS clients so the side-effects are
  exercised against moto fixtures rather than real services.
"""

from panakoes_streaming_router.router import Router, lambda_handler

__all__ = ["Router", "lambda_handler"]
