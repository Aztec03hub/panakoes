"""Panakoes Ingestion API microservice.

Issues pre-signed S3 PUT URLs to authenticated clients and tracks the
resulting upload intents in DynamoDB. The S3 event handler that
finalizes an ingestion when the upload completes lives in a separate
Lambda (next slice).
"""
