"""Internal helpers shared across `panakoes_models` modules.

The reusable pieces are deliberately tiny:

- `BaseDomainModel`: a `BaseModel` subclass that bakes in the project-wide
  `ConfigDict` invariants (`str_strip_whitespace`, `frozen`, `extra="forbid"`)
  so every domain model gets identical strictness without per-class duplication.
- `validate_utc_datetime`: a reusable field validator that requires
  timezone-aware UTC datetimes. Naive datetimes silently misorder events
  across time zones, so we reject them at construction time.

Keeping these in a private module (underscore-prefixed) signals they are
library-internal: consumers should import the concrete domain models, not
the helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


class BaseDomainModel(BaseModel):
    """Project-wide base for every domain model in `panakoes_models`.

    All canonical domain models inherit from this so the cross-cutting
    invariants (whitespace stripping on strings, frozen instances, no
    extra fields) are enforced uniformly. The library is the contract;
    consumers must not be allowed to silently smuggle unknown fields
    through it.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        frozen=True,
        extra="forbid",
    )


def validate_utc_datetime(value: datetime) -> datetime:
    """Require the datetime to be timezone-aware and in UTC.

    Used by every model that exposes a timestamp field. We refuse naive
    datetimes (no tzinfo) and any non-UTC offset because mixed timezones
    are a classic source of cross-service ordering bugs.
    """
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (UTC)")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must be in UTC")
    return value
