"""Panakoes Event Router Lambda.

Processes S3 `ObjectCreated:*` notifications on the audio-uploads bucket,
flips the matching `IngestionRecord` from `pending` to `uploaded` in
DynamoDB, and publishes a downstream `panakoes.ingest / AudioUploaded`
EventBridge event so the transcription pipeline can pick up.
"""

from panakoes_event_router.handler import lambda_handler

__all__ = ["lambda_handler"]
