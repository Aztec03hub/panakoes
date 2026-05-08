"""Outgoing webhook delivery with retry + exponential backoff.

We POST a JSON payload to a caller-supplied URL. Three attempts, base
1s backoff that doubles each retry. Network errors and 5xx responses
trigger a retry; 4xx responses do not (the receiver explicitly said
no, retrying is just noise). HTTPS-only is enforced one layer up at
the request-validation boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of a webhook delivery attempt or attempt-chain."""

    success: bool
    attempts: int
    status_code: int | None
    error: str | None


class WebhookClient:
    """Async HTTP client that POSTs JSON with retry + backoff."""

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_backoff_seconds: float = 1.0,
        request_timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure retry policy and (optional) injected client."""
        self._max_attempts = max(1, max_attempts)
        self._base_backoff = max(0.0, base_backoff_seconds)
        self._timeout = request_timeout_seconds
        self._owned = client is None
        self._client = (
            client if client is not None else httpx.AsyncClient(timeout=request_timeout_seconds)
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client if we own it."""
        if self._owned:
            await self._client.aclose()

    async def post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> WebhookResult:
        """POST `payload` as JSON to `url`. Retry with exponential backoff.

        Returns a `WebhookResult` describing the final outcome. Never
        raises; the caller decides what to do with a failure.
        """
        last_error: str | None = None
        last_status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    url,
                    json=payload,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = None
            else:
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    return WebhookResult(
                        success=True,
                        attempts=attempt,
                        status_code=response.status_code,
                        error=None,
                    )
                last_error = f"HTTP {response.status_code}"
                # Stop retrying on 4xx: the receiver rejected the payload.
                if 400 <= response.status_code < 500:
                    return WebhookResult(
                        success=False,
                        attempts=attempt,
                        status_code=response.status_code,
                        error=last_error,
                    )
            # Backoff before the next attempt, if any.
            if attempt < self._max_attempts:
                await asyncio.sleep(self._base_backoff * (2 ** (attempt - 1)))
        return WebhookResult(
            success=False,
            attempts=self._max_attempts,
            status_code=last_status,
            error=last_error,
        )
