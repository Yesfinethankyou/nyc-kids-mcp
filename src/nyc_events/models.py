"""Domain model: Event + Borough/Price enums + stable ID hashing."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# noqa rationale: (str, Enum) is intentional, not a StrEnum candidate. StrEnum
# changes str()/format() to return the bare value, which would alter how these
# members render in f-strings and serialization — and compute_id hashing and
# pydantic output depend on the current behavior. Don't "modernize" to StrEnum.
class Borough(str, Enum):  # noqa: UP042
    MANHATTAN = "Manhattan"
    BROOKLYN = "Brooklyn"
    QUEENS = "Queens"
    BRONX = "Bronx"
    STATEN_ISLAND = "Staten Island"


class Price(str, Enum):  # noqa: UP042  (see Borough rationale above)
    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


def compute_id(
    source: str,
    external_id: str | None = None,
    url: str | None = None,
    title: str | None = None,
    venue: str | None = None,
    date_iso: str | None = None,
) -> str:
    # start_dt is intentionally NOT part of the hash — a revised time should
    # update the existing row, not create a duplicate that lingers until prune.
    if external_id:
        key = f"{source}|id:{external_id}"
    elif url:
        key = f"{source}|url:{url}"
    else:
        key = f"{source}|tvd:{title or ''}|{venue or ''}|{date_iso or ''}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class Event(BaseModel):
    id: str
    source: str
    external_id: str | None = None
    title: str
    description: str | None = None
    url: str | None = None
    start_dt: datetime
    end_dt: datetime | None = None
    venue_name: str | None = None
    borough: Borough | None = None
    neighborhood: str | None = None
    lat: float | None = None
    lng: float | None = None
    age_min: int | None = None
    age_max: int | None = None
    price: Price = Price.UNKNOWN
    tags: list[str] = Field(default_factory=list)
    # Original upstream JSON row (per-source). Optional: tightly-structured
    # sources (e.g., ICS) don't need it; permit-registry-style sources where
    # the upstream is volatile or rolls off (tvpp-9vvx has a ~30-day window)
    # set this so we can debug field-mapping or recover aged-out detail.
    raw_payload: str | None = None
