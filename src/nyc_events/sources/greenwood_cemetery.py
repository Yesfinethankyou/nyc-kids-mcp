"""Green-Wood Cemetery events.

Green-Wood Cemetery runs a year-round public events programme: walking tours,
nature talks, film screenings, concerts, seasonal festivals, and family
programming. Events come from a WordPress / Tribe Events Calendar REST API;
the fetch/pagination/parsing machinery is shared with the other Tribe sources
via `_tribe.TribeEventsSource`.

This module keeps only what is venue-specific:
  - Kid-relevance strategy: keyword ALLOWLIST against title + excerpt +
    description + categories (categories here are not kid-curated), with the
    shared adult/members-only blocklists as hard title excludes.
  - Tag rules.
  - Venue/borough hardcoded (everything is at Green-Wood, Brooklyn).

Quirks:
  - The `description` field is HTML including inline <style>/<script>/
    <button> elements (Stackable/Eventbrite embeds). The shared strip_html
    drops those elements' contents outright — tag-stripping alone leaks the
    CSS/JS text into the description (this bit us: rows ingested before the
    fix showed ".stk-… {margin…}" as their description preview).
  - The `cost` field is a free-text string ("Free", "$30 / $24 members", "").
  - The API returns `utc_start_date` and `utc_end_date` — used directly.
  - Not all events are kid-appropriate (adult after-hours tours, galas). The
    keyword allowlist gates inclusion; the shared blocklists gate exclusion.
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
    parse_cost,
    parse_row,
    parse_utc_dt,
    strip_html,
)

BASE_URL = "https://www.green-wood.com"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
VENUE_NAME = "Green-Wood Cemetery"
VENUE_ADDRESS = "500 25th Street, Brooklyn, NY 11232"

# Shared Tribe helpers under this module's historical names — the parser tests
# exercise them from here.
_strip_html = strip_html
_parse_utc_dt = parse_utc_dt
_parse_cost = parse_cost

# ---------------------------------------------------------------------------
# Keyword filters
# ---------------------------------------------------------------------------

# An event passes if its title or excerpt contains any allowlist keyword.
_ALLOWLIST_KEYWORDS: tuple[str, ...] = (
    # Family / general
    "family", "families", "kids", "children", "all ages", "drop-in", "workshop",
    "tour", "nature", "garden", "greenhouse", "bird", "wildlife",
    # Film
    "film", "screening", "movie",
    # Music / performance
    "music", "concert", "performance", "band", "orchestra", "choir",
    "sing", "jazz", "acoustic", "live music",
    # Storytelling
    "storytelling", "story time", "storytime", "tales", "folklore", "spoken word",
    # Seasonal / holidays
    "halloween", "haunted", "costume", "spooky", "pumpkin",
    "día de los muertos", "dia de los muertos", "day of the dead", "ofrenda",
    "holiday", "christmas", "hanukkah", "kwanzaa", "lunar new year",
    "diwali", "easter", "cinco de mayo", "carnival", "lantern",
    "winter festival", "spring festival",
)

# An event is dropped unconditionally if its title hits the shared adult
# blocklist or the members-only signal, even when an allowlist keyword also
# matches. Members-only events aren't bookable by the public, so a "birding" or
# "tour" allowlist hit must not pull them back in; "adults only" is a genuine
# adult-only signal that must override the allowlist too ("adults only" also
# covers "for adults only" via substring).
#
# A former soft `_BLOCKLIST_KEYWORDS` list (gala/donor/cocktail/adults only) was
# removed: it was dead code. The allowlist is checked first and short-circuits
# on a hit, and the function's default is a conservative drop, so a soft
# blocklist term was only ever reached on a row that had no allowlist hit —
# already dropped by the default. "gala"/"donor" therefore drop via the default
# (a "Family Gala" with an allowlist hit was kept before and still is), and the
# real adult-only signals moved to the shared `ADULT_BLOCKLIST` so they take
# effect.

# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------

_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("family", ("family", "families", "all ages", "drop-in", "caregiver")),
    ("nature", ("nature", "garden", "greenhouse", "bird", "wildlife", "wasp", "insect", "tree")),
    ("music", ("music", "concert", "performance", "band", "orchestra", "choir", "sing-along",
               "singalong", "jazz", "acoustic", "live music")),
    ("educational", ("workshop", "tour", "talk", "lecture", "history", "science")),
    ("movie", ("film", "screening", "movie")),
    ("storytelling", ("storytelling", "story time", "storytime", "tales", "folklore",
                      "spoken word")),
    ("halloween", ("halloween", "haunted", "costume", "spooky", "pumpkin")),
    ("holiday", ("holiday", "christmas", "hanukkah", "kwanzaa", "lunar new year", "diwali",
                 "easter", "cinco de mayo", "carnival", "lantern", "winter festival",
                 "spring festival", "día de los muertos", "dia de los muertos",
                 "day of the dead", "ofrenda")),
    ("best for kids", ("kids", "children", "family", "families", "all ages")),
]


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Return True if the event passes the kid-relevance filter."""
    title = (row.get("title") or "").lower()
    excerpt = _strip_html(row.get("excerpt")).lower()
    description = _strip_html(row.get("description")).lower()
    # Also incorporate category names as signal.
    cats = " ".join(c.get("name", "").lower() for c in (row.get("categories") or []))
    haystack = f"{title} {excerpt} {description} {cats}"

    # Hard exclusions on title win over the allowlist.
    if (
        contains_any(title, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
        or contains_any(title, MEMBERS_ONLY)
    ):
        return False

    # Check allowlist first.
    for kw in _ALLOWLIST_KEYWORDS:
        if kw in haystack:
            return True

    # No allowlist match — conservative default: drop.
    return False


def _infer_tags(title: str, description: str | None) -> list[str]:
    """Infer tags from title + description keywords."""
    haystack = title.lower() + " " + (description or "").lower()
    tags: list[str] = ["family"]
    for tag, keywords in _TAG_RULES:
        # Leading word boundary: "tree" matches "trees" but not "street";
        # prefixes still match.
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", haystack) for kw in keywords
        ):
            tags.append(tag)
    return tags


def _build_event(row: dict[str, Any], p: RowParts) -> Event:
    return Event(
        id=compute_id("greenwood_cemetery", external_id=p.external_id, url=p.url, title=p.title),
        source="greenwood_cemetery",
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
        tags=_infer_tags(p.title, p.description),
        raw_payload=p.raw_payload,
    )


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    return parse_row(
        row,
        source="greenwood_cemetery",
        is_kid_relevant=_is_kid_relevant,
        build_event=_build_event,
    )


class GreenWoodCemeterySource(TribeEventsSource):
    """Green-Wood Cemetery public events via the Tribe Events Calendar REST API."""

    name = "greenwood_cemetery"
    events_url = EVENTS_URL
    max_pages = 60  # safety cap; ~200 events / 50 per page = 4 pages in practice
    _parse_row = staticmethod(_parse_row)
