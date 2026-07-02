"""Industry City events.

Industry City (a 35-acre former manufacturing complex on the Sunset Park,
Brooklyn waterfront) hosts a busy public events calendar: maker/craft
workshops, family programming (e.g. the Puppetworks marionette theatre),
food-hall pop-ups, ticketed culinary tours, outdoor watch parties, plus a
fair amount of adult nightlife (21+ shows, drink tastings). Events come from
a WordPress / The Events Calendar (Tribe) REST API; the fetch/pagination/
parsing machinery is shared with the other Tribe sources via
`_tribe.TribeEventsSource`.

This module keeps only what is venue-specific:
  - Kid-relevance strategy: title/description KEYWORDS (categories here are
    NOT kid-curated — see below), with `Nightlife` as a hard-exclude category
    and an adult-content blocklist that wins over any allowlist hit.
  - Tag rules.
  - Venue/borough hardcoded; price always UNKNOWN (see quirks).

Quirks (verified live + against the captured fixture, 2026-06-20):
  - The Tribe `id` IS per-occurrence: the same precedent as Prospect Park and
    NY Transit Museum holds here. Recurring events (the "Industry City Gourmet
    Food and Drinks Tour" appears once per date) get a distinct `id` AND a
    dated URL slug (`.../industry-city-gourmet-food-and-drinks-tour/2026-06-27/`)
    per occurrence — in the 15-row fixture, all 15 ids are distinct across the
    two tour occurrences. So `external_id = str(id)` is safe; no
    `:start.isoformat()` suffix needed.
  - `cost` is ALWAYS empty upstream (confirmed across the fixture) → price is
    `UNKNOWN` for every row.
  - `venue` is ALWAYS an empty list upstream → venue and borough are
    hardcoded ("Industry City" / Brooklyn). No per-event lat/lng, no age range.
  - Categories are sparse and NOT kid-curated: only `Ticketed Events`,
    `Workshops`, `Nightlife`, `Tours`, plus ~10% uncategorized — and the real
    kids' puppet show is uncategorized. A category allowlist alone would wrongly
    drop kid events, so kid-relevance is decided by title/description keywords.
  - Adult programming (21+ band nights, burlesque/drag, late-night shows) is
    dropped via the hard-exclude blocklist, which wins over any allowlist hit.
    Alcohol-tasting terms are NOT blocklisted — alcohol at a venue isn't by
    itself an adult-only signal. The outdoor "World Cup Watch
    Party" rows say "NO STROLLERS or children under 3"; the word "children"
    matches the allowlist, so they are KEPT as family-friendly outdoor events.
    We deliberately do NOT blocklist "no strollers" / "children under the age"
    — those phrasings also catch legit kid events that merely ban strollers or
    price admission by age.
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
  - `window_days = 60` (full-window re-fetch → opted into missing-detection).
"""

from __future__ import annotations

from typing import Any

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, ADULT_TITLE_BLOCKLIST, contains_any
from ._tribe import (
    RowParts,
    TribeEventsSource,
    category_names,
    parse_row,
    parse_utc_dt,
    strip_html,
)

BASE_URL = "https://industrycity.com"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
VENUE_NAME = "Industry City"

# Shared Tribe helpers under this module's historical names — the parser tests
# exercise them from here.
_strip_html = strip_html
_parse_utc_dt = parse_utc_dt
_category_names = category_names

# ---------------------------------------------------------------------------
# Keyword filters
#
# Categories upstream are not kid-curated (and the real kids' puppet show is
# uncategorized), so kid-relevance is keyword-driven on title + description +
# excerpt. Mirrors Green-Wood's approach.
# ---------------------------------------------------------------------------

# An event passes if its title/description/excerpt contains any of these.
_ALLOWLIST_KEYWORDS: tuple[str, ...] = (
    # Family / general
    "family", "families", "kids", "children", "all ages", "all-ages",
    "drop-in", "drop in", "toddler", "stroller-friendly",
    # Hands-on / making
    "workshop", "craft", "make your own", "mending", "diy", "hands-on",
    # Arts / performance for kids
    "puppet", "puppetry", "marionette", "storytime", "story time",
    "storytelling", "art class", "kids art",
    # Outings that are usually kid-OK at this venue
    "market", "flea", "garden", "open studio",
)

# Hard exclusions — win over any allowlist hit. The shared `ADULT_BLOCKLIST`
# (21+, burlesque, drag show/brunch, "no children", etc.) plus this source's
# only local extra, "late night", flag adult content that would otherwise be
# pulled back in by a "tour"/"class"/"workshop" match. Alcohol-tasting terms
# (cocktail/whiskey/sake/brewery/distillery/wine-or-beer tasting/happy hour)
# were intentionally removed: alcohol at a venue is not by itself an adult-only
# signal, and they dropped legitimate family events (e.g. food-and-drink
# markets). "no children" is the only "no …" phrasing kept — the weaker "no
# strollers" / "children under the age" wrongly dropped legit kid events (the
# outdoor World Cup watch parties read "NO STROLLERS or children under … 3").
_LOCAL_EXCLUDE: tuple[str, ...] = ("late night",)

# Upstream categories that are hard-exclude regardless of keywords.
_EXCLUDE_CATEGORIES: frozenset[str] = frozenset({"Nightlife"})

# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------

_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("best for kids", ("kids", "children", "family", "families", "all ages",
                       "all-ages", "toddler")),
    ("arts and crafts", ("craft", "workshop", "make your own", "mending", "diy",
                         "hands-on", "art class", "open studio")),
    ("puppets", ("puppet", "puppetry", "marionette")),
    ("storytelling", ("storytime", "story time", "storytelling")),
    ("market", ("market", "flea", "open studio")),
    ("outdoors", ("garden", "outdoor", "courtyard")),
]


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Return True if the event passes the keyword-based kid-relevance filter.

    Exclusion categories and the hard-exclude blocklist win over the allowlist.
    """
    title = _strip_html(row.get("title")).lower()
    excerpt = _strip_html(row.get("excerpt")).lower()
    description = _strip_html(row.get("description")).lower()
    haystack = f"{title} {excerpt} {description}"

    if _EXCLUDE_CATEGORIES & _category_names(row):
        return False
    if (
        contains_any(haystack, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
        or contains_any(haystack, _LOCAL_EXCLUDE)
    ):
        return False
    return any(kw in haystack for kw in _ALLOWLIST_KEYWORDS)


def _infer_tags(title: str, description: str | None) -> list[str]:
    """Infer tags from title + description keywords."""
    haystack = title.lower() + " " + (description or "").lower()
    tags: list[str] = ["family"]
    for tag, keywords in _TAG_RULES:
        if tag not in tags and any(kw in haystack for kw in keywords):
            tags.append(tag)
    return tags


def _build_event(row: dict[str, Any], p: RowParts) -> Event:
    return Event(
        id=compute_id("industry_city", external_id=p.external_id, url=p.url, title=p.title),
        source="industry_city",
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
        price=Price.UNKNOWN,  # cost is always empty upstream
        tags=_infer_tags(p.title, p.description_text),
        raw_payload=p.raw_payload,
    )


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    return parse_row(
        row,
        source="industry_city",
        is_kid_relevant=_is_kid_relevant,
        build_event=_build_event,
    )


class IndustryCitySource(TribeEventsSource):
    """Industry City events via the Tribe Events Calendar REST API."""

    name = "industry_city"
    events_url = EVENTS_URL
    max_pages = 30  # safety cap; ~195 events / 50 per page = 4 pages in practice
    _parse_row = staticmethod(_parse_row)
