"""Prospect Park Alliance events.

The Prospect Park Alliance publishes the full park calendar — nature
programming (Audubon Center, Pop-Up Audubon), the Carousel, Lefferts Historic
House, films, performances, plus plenty of non-kid programming (yoga,
greenmarkets, fun runs). Events come from a WordPress / Tribe Events Calendar
REST API; the fetch/pagination/parsing machinery is shared with the other
Tribe sources via `_tribe.TribeEventsSource`.

This module keeps only what is venue-specific:
  - Kid-relevance strategy: upstream `categories` against a kid-relevant
    ALLOWLIST (unlike Green-Wood's keyword filter — this site's categories
    are reliable), with the shared adult/members-only blocklists as a
    defensive title hard-exclude net (none currently trigger in live data).
  - Tag rules (category-driven, title keywords as a supplement).
  - Venue/borough hardcoded (`venue` is always an empty list upstream).

Quirks (verified live, 2026-06):
  - The Tribe `id` IS per-occurrence: recurring events ("Carousel Rides",
    "Nature Exploration") get a distinct id and a dated URL slug per
    occurrence, so `external_id = str(id)` is safe — no `:start.isoformat()`
    suffix needed (456 events in a 60-day window → 456 distinct ids/urls).
  - `cost` is free text: "Free", "Free, RSVP!", "FREE, RSVP", "$3 – $13",
    "Prices Vary", or empty.
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import Borough, Event, compute_id
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

BASE_URL = "https://www.prospectpark.org"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
VENUE_NAME = "Prospect Park"

# Shared Tribe helpers under this module's historical names — the parser tests
# exercise them from here.
_strip_html = strip_html
_parse_utc_dt = parse_utc_dt
_parse_cost = parse_cost
_category_names = category_names

# ---------------------------------------------------------------------------
# Category filter
# ---------------------------------------------------------------------------

# An event passes if any of its upstream category names is in this set.
# Names verified against live data 2026-06 (counts in a 60-day window:
# Kids=124, Audubon Center=176, Nature Programs=95, Lefferts=107,
# Carousel=17, Education=18, Performing Arts=8, Film=4).
_INCLUDE_CATEGORIES: frozenset[str] = frozenset(
    {
        "Kids",
        "Audubon Center",
        "Carousel",
        "Lefferts Historic House",
        "Nature Programs",
        "Film",
        "Performing Arts",
        "Education",
    }
)

# Defensive net: drop unconditionally if the title hits the shared adult
# blocklist or the members-only signal, even when an included category matches.
# No live events currently trigger this — it guards against adult programming
# slipping into broad categories like "Performing Arts" or "Film".

# ---------------------------------------------------------------------------
# Tag inference (category-driven, with title keywords as a supplement)
# ---------------------------------------------------------------------------

_CATEGORY_TAGS: dict[str, str] = {
    "Kids": "best for kids",
    "Audubon Center": "nature",
    "Pop-Up Audubon": "nature",
    "Nature Programs": "nature",
    "Birdwatching": "nature",
    "Environment": "nature",
    "Carousel": "carousel",
    "Lefferts Historic House": "educational",
    "Education": "educational",
    "History": "educational",
    "Tours": "educational",
    "Film": "movie",
    "Performing Arts": "music",
    "Music": "music",
    "Holiday": "holiday",
}

_TITLE_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("nature", ("nature", "bird", "wildlife", "pollinator", "audubon")),
    ("music", ("music", "concert", "performance", "dj ", "sing")),
    ("movie", ("film", "screening", "movie", "cinema")),
    ("holiday", ("juneteenth", "fourth of july", "holiday", "halloween")),
]


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Return True if the event passes the category-based kid-relevance filter."""
    title = _strip_html(row.get("title")).lower()
    if (
        contains_any(title, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
        or contains_any(title, MEMBERS_ONLY)
    ):
        return False
    return bool(_INCLUDE_CATEGORIES & _category_names(row))


def _infer_tags(title: str, categories: set[str]) -> list[str]:
    """Infer tags from upstream categories plus title keywords."""
    tags: list[str] = ["family"]
    for cat, tag in _CATEGORY_TAGS.items():
        if cat in categories and tag not in tags:
            tags.append(tag)
    title_lower = title.lower()
    for tag, keywords in _TITLE_TAG_RULES:
        # Leading word boundary: "sing" matches "singing"/"sing-along" but not
        # "crossing"/"housing"; prefixes still match.
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", title_lower) for kw in keywords
        ):
            tags.append(tag)
    return tags


def _build_event(row: dict[str, Any], p: RowParts) -> Event:
    return Event(
        id=compute_id("prospect_park", external_id=p.external_id, url=p.url, title=p.title),
        source="prospect_park",
        external_id=p.external_id,
        title=p.title,
        description=p.description,
        url=p.url,
        start_dt=p.start_dt,
        end_dt=p.end_dt,
        venue_name=VENUE_NAME,
        borough=Borough.BROOKLYN,
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=_parse_cost(row.get("cost")),
        tags=_infer_tags(p.title, _category_names(row)),
        raw_payload=p.raw_payload,
    )


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    return parse_row(
        row,
        source="prospect_park",
        is_kid_relevant=_is_kid_relevant,
        build_event=_build_event,
    )


class ProspectParkSource(TribeEventsSource):
    """Prospect Park Alliance events via the Tribe Events Calendar REST API."""

    name = "prospect_park"
    events_url = EVENTS_URL
    max_pages = 30  # safety cap; ~456 events / 50 per page = 10 pages in practice
    _parse_row = staticmethod(_parse_row)
