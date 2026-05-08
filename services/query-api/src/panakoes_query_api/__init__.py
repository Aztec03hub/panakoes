"""Panakoes Query API microservice.

Unified read API over the user's ingestions, summaries, and streaming
sessions. Backs the user-facing dashboard. Every endpoint is JWT
validated and owner-scoped: cross-user reads collapse to 404 so we
never leak the existence of records we do not own.
"""
