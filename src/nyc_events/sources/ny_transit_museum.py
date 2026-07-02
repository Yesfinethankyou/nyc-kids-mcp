"""New York Transit Museum events.

The Transit Museum (Downtown Brooklyn, in a decommissioned 1936 subway
station) runs toddler programs (Transit Tots), family workshops (Movers and
Makers), vintage-train Nostalgia Rides, plus adult walking tours, lectures
and virtual talks. Events come from a WordPress / Tribe Events Calendar REST
API; the fetch/pagination/parsing machinery is shared with the other Tribe
sources via `_tribe.TribeEventsSource`.

This module keeps only what is venue-specific:
  - Kid-relevance strategy: upstream `categories` — allowlist {Family
    Programs, Nostalgia Rides}; hard-exclude {Members-Only Programs, Virtual
    Programs} (exclusion wins over any allowlist overlap).
  - The per-event `venue` object → venue_name / borough / lat / lng mapping
    (unlike the other Tribe sources, venue is a real object here).
  - A cost quirk: "Included with Museum admission" maps to PAID.
  - Tag rules.

Quirks (verified live, 2026-06-10):
  - The Tribe `id` IS per-occurrence: 26 events in a 60-day window → 26
    distinct ids, with recurring programs (Transit Tots ×7, Old City Hall
    tour ×3, shuttle rides ×2) each getting a distinct id and dated URL
    slug per occurrence. `external_id = str(id)` — no `:start.isoformat()`
    suffix needed.
  - Venue values seen live: "New York Transit Museum, Brooklyn" (city=
    "Brooklyn", geo_lat/geo_lng populated), "Off-Site" (subway/station
    tours meeting elsewhere — no city, no geo), "Virtual" (no city/geo).
    Borough comes from the venue `city` field when recognized; otherwise
    None — we don't guess for Off-Site events.
  - `description` is empty on the list endpoint; the text lives in
    `excerpt` (the shared `excerpt or description` preference handles it).
  - `cost` is populated: "$40", "$35 – $40", "Free", and "Included with
    Museum admission" (mapped to PAID — museum admission is paid).
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
  - Known dropped edge cases (recorded, deliberate): "Subway Simulator
    Sunday" has NO categories and "Special Day" (sensory-friendly program
    for children with disabilities) is categorized only "Access Programs" —
    both kid-relevant but outside the allowlist. Widen the allowlist if
    these matter; don't silently special-case titles.
  - "Special Event" category only co-occurred with "Nostalgia Rides" in
    live data, so it adds nothing and is left off the allowlist.
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

BASE_URL = "https://www.nytransitmuseum.org"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"

# Shared Tribe helpers under this module's historical names — the parser tests
# exercise them from here.
_strip_html = strip_html
_parse_utc_dt = parse_utc_dt
_category_names = category_names

# ---------------------------------------------------------------------------
# Category filter
# ---------------------------------------------------------------------------

# An event passes if any of its upstream category names is in this set.
# Live 60-day counts (2026-06-10): Family Programs=8 (Transit Tots, Movers
# and Makers), Nostalgia Rides=2 (vintage shuttle rides). Adult Tours /
# Lectures / Curator Talks fall out of the allowlist naturally.
_INCLUDE_CATEGORIES: frozenset[str] = frozenset(
    {
        "Family Programs",
        "Nostalgia Rides",
    }
)

# Hard category exclusion — wins over any allowlist overlap. An event
# carrying both "Family Programs" and "Members-Only Programs" must be
# dropped (single-user server, no museum membership assumed; virtual
# programs aren't outings).
_EXCLUDE_CATEGORIES: frozenset[str] = frozenset(
    {
        "Members-Only Programs",
        "Virtual Programs",
    }
)

# Defensive net: drop unconditionally if the title hits the shared adult
# blocklist or the members-only signal, even when an included category matches.
# No live events currently trigger this — same guard as prospect_park.

# ---------------------------------------------------------------------------
# Venue / borough mapping
# ---------------------------------------------------------------------------

_CITY_BOROUGH: dict[str, Borough] = {
    "brooklyn": Borough.BROOKLYN,
    "manhattan": Borough.MANHATTAN,
    "new york": Borough.MANHATTAN,
    "queens": Borough.QUEENS,
    "bronx": Borough.BRONX,
    "the bronx": Borough.BRONX,
    "staten island": Borough.STATEN_ISLAND,
}

# ---------------------------------------------------------------------------
# Tag inference (category-driven, with title keywords as a supplement)
# ---------------------------------------------------------------------------

_CATEGORY_TAGS: dict[str, str] = {
    "Family Programs": "best for kids",
    "Nostalgia Rides": "trains",
    "Tours": "educational",
    "Special Event": "special event",
}

_TITLE_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("trains", ("subway", "train", "trolley", "shuttle", "bus", "transit")),
    ("educational", ("workshop", "tour", "story", "craft", "maker")),
    ("best for kids", ("tots", "kids", "children", "family")),
]


def _parse_cost(cost: str | None) -> Price:
    """Shared Tribe cost mapping plus this venue's "Included with Museum
    admission" phrasing — admission itself is paid."""
    price = parse_cost(cost)
    if price is Price.UNKNOWN and cost and "admission" in cost.lower():
        return Price.PAID
    return price


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Return True if the event passes the category-based kid-relevance filter.

    Exclusion categories and title hard-excludes win over the allowlist.
    """
    title = _strip_html(row.get("title")).lower()
    if (
        contains_any(title, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
        or contains_any(title, MEMBERS_ONLY)
    ):
        return False
    categories = _category_names(row)
    if _EXCLUDE_CATEGORIES & categories:
        return False
    return bool(_INCLUDE_CATEGORIES & categories)


def _venue_fields(
    row: dict[str, Any],
) -> tuple[str | None, Borough | None, float | None, float | None]:
    """Map the per-event Tribe venue object to (venue_name, borough, lat, lng).

    Borough comes from the venue's `city` when recognized; "Off-Site" and
    "Virtual" venues have no city/geo upstream and yield None — we don't
    guess where a subway tour meets.
    """
    venue = row.get("venue")
    if not isinstance(venue, dict):
        return None, None, None, None
    name = _strip_html(venue.get("venue")) or None
    city = (venue.get("city") or "").strip().lower()
    borough = _CITY_BOROUGH.get(city)
    lat = venue.get("geo_lat")
    lng = venue.get("geo_lng")
    lat = float(lat) if isinstance(lat, int | float) else None
    lng = float(lng) if isinstance(lng, int | float) else None
    return name, borough, lat, lng


def _infer_tags(title: str, categories: set[str]) -> list[str]:
    """Infer tags from upstream categories plus title keywords."""
    tags: list[str] = ["family"]
    for cat, tag in _CATEGORY_TAGS.items():
        if cat in categories and tag not in tags:
            tags.append(tag)
    title_lower = title.lower()
    for tag, keywords in _TITLE_TAG_RULES:
        # Leading word boundary stops short keywords from matching mid-word:
        # "bus" no longer hits "business", "story" no longer hits "history";
        # prefixes still match.
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", title_lower) for kw in keywords
        ):
            tags.append(tag)
    return tags


def _build_event(row: dict[str, Any], p: RowParts) -> Event:
    venue_name, borough, lat, lng = _venue_fields(row)
    return Event(
        id=compute_id("ny_transit_museum", external_id=p.external_id, url=p.url, title=p.title),
        source="ny_transit_museum",
        external_id=p.external_id,
        title=p.title,
        description=p.description,
        url=p.url,
        start_dt=p.start_dt,
        end_dt=p.end_dt,
        venue_name=venue_name,
        borough=borough,
        lat=lat,
        lng=lng,
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
        source="ny_transit_museum",
        is_kid_relevant=_is_kid_relevant,
        build_event=_build_event,
    )


class NYTransitMuseumSource(TribeEventsSource):
    """New York Transit Museum events via the Tribe Events Calendar REST API."""

    name = "ny_transit_museum"
    events_url = EVENTS_URL
    max_pages = 10  # safety cap; ~26 events / 60 days = 1 page in practice
    _parse_row = staticmethod(_parse_row)
