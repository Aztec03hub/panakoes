"""Panakoes Transcribe Worker Lambda.

Consumes the SQS queue that EventBridge fans S3 ObjectCreated events
into, parses each S3 object key back into `(user_id, ingestion_id)`,
and invokes `panakoes_ingestion_api.transcription.transcribe_ingestion`
to fire the existing on-demand transcription orchestration. The API
route and this worker share the same orchestration so transcription
behavior stays single-sourced.
"""

from panakoes_transcribe_worker.handler import lambda_handler

__all__ = ["lambda_handler"]
