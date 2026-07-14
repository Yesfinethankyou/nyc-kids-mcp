"""City Parks Foundation events.

City Parks Foundation (CPF) is the nonprofit behind **SummerStage** (free
outdoor concerts across NYC parks), the **PuppetMobile** (free touring puppet
shows, explicitly kids' programming), and citywide park programs. Events come
from a WordPress / The Events Calendar (Tribe) REST API; the fetch/pagination/
parsing machinery is shared with the other Tribe sources via
`_tribe.TribeEventsSource`.

This module keeps only what is venue-specific:
  - Kid-relevance strategy: a **category allowlist** — `PuppetMobile` (kids'
    puppet shows) and `SummerStage` (free park concerts). Everything else in
    the feed is CPF's operational programming — `Volunteer: It's My Park`,
    `Grants and More`, `Partnerships for Parks` — and is dropped.
  - **All SummerStage rows are kept unconditionally (maintainer call,
    2026-07-13).** Unlike the open-calendar Tribe sources (Industry City),
    CPF does NOT get the shared `ADULT_BLOCKLIST` gate: these two categories
    are hand-curated CPF programs and the maintainer wants every SummerStage
    show, so a keyword like "21+" in a concert blurb must not drop it.
  - Borough is **per-event** (CPF is a citywide multi-park aggregator).
  - Price/tags.

Quirks (verified live + against the captured fixture, 2026-07-13):
  - The Tribe `id` IS per-occurrence: recurring programs (the PuppetMobile
    "Pinocchio" tour plays ~9 dates across all five boroughs) get a distinct
    `id` per date, so `external_id = str(id)` needs no `:start` suffix — same
    precedent as the other Tribe sources.
  - **`venue.venue` holds the BOROUGH string, not a park name**
    ("Manhattan" / "Brooklyn" / "Queens" / "Bronx" / "Staten Island" /
    "Online"), and its `url` is `.../venue/<borough>/`. `city`/`state`/
    `address`/`zip`/`geo_lat`/`geo_lng` are all null. So borough is derived
    from `venue.venue`; there is **no structured park name**, so
    `venue_name` is left None and neighborhood coding does not resolve for
    CPF rows (acceptable — the borough + event URL carry the location, and
    None is the neighborhood status quo). Rows whose venue is `Online` /
    `is_virtual` are dropped (not a physical NYC event) — in practice these
    are the excluded-category grant sessions anyway.
  - `custom_fields` is always empty upstream.
  - `cost` is "Free" for SummerStage/PuppetMobile → price FREE.
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
  - `window_days = 60` (full-window re-fetch → opted into missing-detection).
"""

from __future__ import annotations

from typing import Any

from ..models import Borough, Event, compute_id
from ._tribe import (
    RowParts,
    TribeEventsSource,
    category_names,
    parse_cost,
    parse_row,
    parse_utc_dt,
    strip_html,
)

BASE_URL = "https://cityparksfoundation.org"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"

# Shared Tribe helpers under this module's names — the parser tests exercise
# them from here.
_strip_html = strip_html
_parse_utc_dt = parse_utc_dt
_category_names = category_names

# Category allowlist: the two hand-curated kid/family CPF programs. Everything
# else in the feed is operational (volunteer days, grants, partnerships).
_ALLOWLIST_CATEGORIES: frozenset[str] = frozenset({"PuppetMobile", "SummerStage"})

# venue.venue → Borough. CPF stores the borough as the "venue" name.
_BOROUGH_BY_VENUE: dict[str, Borough] = {
    "manhattan": Borough.MANHATTAN,
    "brooklyn": Borough.BROOKLYN,
    "queens": Borough.QUEENS,
    "bronx": Borough.BRONX,
    "staten island": Borough.STATEN_ISLAND,
}

# Tag inference. Base "family" mirrors the other curated sources; the rest are
# keyed off the (single, known) category.
_CATEGORY_TAGS: dict[str, tuple[str, ...]] = {
    "PuppetMobile": ("best for kids", "puppet", "performance", "outdoors"),
    "SummerStage": ("music", "concert", "outdoors"),
}


def _borough_for(row: dict[str, Any]) -> Borough | None:
    venue = row.get("venue") or {}
    return _BOROUGH_BY_VENUE.get((venue.get("venue") or "").strip().lower())


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Keep rows in the PuppetMobile / SummerStage categories that have a real
    (non-virtual) NYC borough venue. No keyword/adult filtering — these are
    curated CPF programs and the maintainer wants all of them (see docstring).
    """
    if row.get("is_virtual"):
        return False
    if not (_ALLOWLIST_CATEGORIES & _category_names(row)):
        return False
    return _borough_for(row) is not None


def _infer_tags(row: dict[str, Any]) -> list[str]:
    tags: list[str] = ["family"]
    for cat in _category_names(row):
        for t in _CATEGORY_TAGS.get(cat, ()):
            if t not in tags:
                tags.append(t)
    return tags


def _build_event(row: dict[str, Any], p: RowParts) -> Event:
    return Event(
        id=compute_id("city_parks_foundation", external_id=p.external_id, url=p.url, title=p.title),
        source="city_parks_foundation",
        external_id=p.external_id,
        title=p.title,
        description=p.description,
        url=p.url,
        start_dt=p.start_dt,
        end_dt=p.end_dt,
        venue_name=None,  # only borough is available upstream (see docstring)
        borough=_borough_for(row),
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=parse_cost(row.get("cost")),
        tags=_infer_tags(row),
        raw_payload=p.raw_payload,
    )


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    return parse_row(
        row,
        source="city_parks_foundation",
        is_kid_relevant=_is_kid_relevant,
        build_event=_build_event,
    )


class CityParksFoundationSource(TribeEventsSource):
    """City Parks Foundation events via the Tribe Events Calendar REST API."""

    name = "city_parks_foundation"
    display_name = "City Parks Foundation"
    events_url = EVENTS_URL
    max_pages = 30  # safety cap; ~82 events / 50 per page = 2 pages in practice
    _parse_row = staticmethod(_parse_row)
