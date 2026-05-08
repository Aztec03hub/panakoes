"""Panakoes Session Manager microservice.

Tracks the lifecycle of live streaming-transcription sessions: creation,
status transitions, and TTL-bound retention. State lives in the
`panakoes-*-streaming-sessions` DynamoDB table; the GPU spawner that
backs an active session lives in a separate Lambda (next slice).
"""
