"""Staten Island Children's Museum events.

The Staten Island Children's Museum (Snug Harbor Cultural Center campus,
1000 Richmond Terrace) publishes its program calendar via a WordPress /
The Events Calendar (Tribe) REST API — the fifth source on the shared
`_tribe.TribeEventsSource` machinery. Highest-value add of the Phase 3
venue batch: Staten Island previously had near-zero catalog coverage.

This module keeps only what is venue-specific:
  - Kid-relevance strategy: NONE by construction — a children's museum's
    own calendar is a curated kids feed (same posture as
    `bk_childrens_museum`/`mommy_poppins`). The shared adult/members-only
    title net is kept as a defensive guard only (children's museums do run
    occasional 21+ "adults night" fundraisers; none in live data 2026-07-13
    — all 64 titles spot-checked as kid programming).
  - Tag rules (category-driven — the Tribe taxonomy here is kid-curated:
    "Event for Kids", "Family Friendly", "STEM", "Art-Making", …).
  - Venue/borough hardcoded (single site; upstream venue object confirms).
  - Price: `cost` is empty on every live row, but the museum applies a
    "Free" *category* to free-admission events — that category maps to
    Price.FREE, everything else stays UNKNOWN (admission-included programs).

Quirks (verified live, 2026-07-13):
  - The Tribe `id` IS per-occurrence: recurring programs ("Walk-In!
    Workshop: Postage Stamps") get a distinct id and dated URL slug per
    occurrence (ids 9400/9413/9414/… each with its own start_date), so
    `external_id = str(id)` is safe — same as the other four Tribe venues.
  - 64 events in the near-term window at probe time; 2 pages at
    per_page=50.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import Borough, Event, Price, compute_id
from ._filters import (
    ADULT_BLOCKLIST,
    ADULT_TITLE_BLOCKLIST,
    MEMBERS_ONLY,
    contains_any,
)
from ._tribe import (
    RowParts,
    TribeEventsSource,
    category_names,
    parse_cost,
    parse_row,
    parse_utc_dt,
    strip_html,
)

BASE_URL = "https://sichildrensmuseum.org"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
VENUE_NAME = "Staten Island Children's Museum"

# Shared Tribe helpers under local names — the parser tests exercise them
# from here (same convention as the other Tribe source modules).
_strip_html = strip_html
_parse_utc_dt = parse_utc_dt
_parse_cost = parse_cost
_category_names = category_names

# ---------------------------------------------------------------------------
# Tag inference (category-driven; the taxonomy is venue-curated and reliable)
# ---------------------------------------------------------------------------

_CATEGORY_TAGS: dict[str, str] = {
    "Event for Kids": "best for kids",
    "Family Friendly": "best for kids",
    "art": "arts & crafts",
    "Art-Making": "arts & crafts",
    "crafts": "arts & crafts",
    "STEM": "science",
    "STEAM": "science",
    "Music": "music",
    "Dance": "dance",
    "Cooking": "cooking",
    "literacy": "story time",
    "reading": "story time",
    "holiday": "holiday",
}

_TITLE_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("story time", ("storytime", "story time")),
    ("science", ("science", "stem")),
    ("music", ("music", "boogie", "dance")),
]


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Curated kids feed — everything passes except the shared adult/
    members-only title net (defensive only; see module docstring)."""
    title = _strip_html(row.get("title")).lower()
    return not (
        contains_any(title, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
        or contains_any(title, MEMBERS_ONLY)
    )


def _resolve_price(row: dict[str, Any], categories: set[str]) -> Price:
    """`cost` is empty on every live row; the venue's "Free" category is the
    real free-admission signal. A non-empty cost string still wins if one
    ever appears upstream."""
    price = _parse_cost(row.get("cost"))
    if price is Price.UNKNOWN and "Free" in categories:
        return Price.FREE
    return price


def _infer_tags(title: str, categories: set[str]) -> list[str]:
    """Infer tags from upstream categories plus title keywords."""
    tags: list[str] = ["family"]
    for cat, tag in _CATEGORY_TAGS.items():
        if cat in categories and tag not in tags:
            tags.append(tag)
    title_lower = title.lower()
    for tag, keywords in _TITLE_TAG_RULES:
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", title_lower) for kw in keywords
        ):
            tags.append(tag)
    return tags


def _build_event(row: dict[str, Any], p: RowParts) -> Event:
    categories = _category_names(row)
    return Event(
        id=compute_id(
            "si_childrens_museum", external_id=p.external_id, url=p.url, title=p.title
        ),
        source="si_childrens_museum",
        external_id=p.external_id,
        title=p.title,
        description=p.description,
        url=p.url,
        start_dt=p.start_dt,
        end_dt=p.end_dt,
        venue_name=VENUE_NAME,
        borough=Borough.STATEN_ISLAND,
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=_resolve_price(row, categories),
        tags=_infer_tags(p.title, categories),
        raw_payload=p.raw_payload,
    )


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    return parse_row(
        row,
        source="si_childrens_museum",
        is_kid_relevant=_is_kid_relevant,
        build_event=_build_event,
    )


class SIChildrensMuseumSource(TribeEventsSource):
    """Staten Island Children's Museum events via the Tribe REST API."""

    name = "si_childrens_museum"
    events_url = EVENTS_URL
    max_pages = 10  # safety cap; ~64 events / 50 per page = 2 pages in practice
    _parse_row = staticmethod(_parse_row)
