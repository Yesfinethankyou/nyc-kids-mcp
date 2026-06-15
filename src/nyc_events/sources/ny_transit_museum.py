"""New York Transit Museum events.

The Transit Museum (Downtown Brooklyn, in a decommissioned 1936 subway
station) runs toddler programs (Transit Tots), family workshops (Movers and
Makers), vintage-train Nostalgia Rides, plus adult walking tours, lectures
and virtual talks. Events come from a WordPress / Tribe Events Calendar REST
API — the third Tribe instance in this project (after Green-Wood Cemetery
and Prospect Park); this module is a copy-adapt of `prospect_park.py`.

Data flow:
  1. GET /wp-json/tribe/events/v1/events?per_page=50&page=N (curl_cffi with
     Chrome impersonation — plain default-UA fetchers get 403).
  2. Paginate via `next_rest_url` in each response until absent. Small
     calendar (~26 events / 60 days) — single page in practice, but the
     loop is kept.
  3. Filter by upstream `categories`: allowlist {Family Programs,
     Nostalgia Rides}; hard-exclude {Members-Only Programs, Virtual
     Programs} (exclusion wins over any allowlist overlap).
  4. Map the per-event `venue` object to venue_name / borough / lat / lng.
  5. Strip HTML, map the cost string to a Price enum, yield Events.

Quirks (verified live, 2026-06-10):
  - The Tribe `id` IS per-occurrence: 26 events in a 60-day window → 26
    distinct ids, with recurring programs (Transit Tots ×7, Old City Hall
    tour ×3, shuttle rides ×2) each getting a distinct id and dated URL
    slug per occurrence. `external_id = str(id)` — no `:start.isoformat()`
    suffix needed.
  - Unlike Prospect Park, `venue` is a real per-event object, not an empty
    list. Values seen live: "New York Transit Museum, Brooklyn" (city=
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

import json
import logging
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from curl_cffi import requests as cffi_requests

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nytransitmuseum.org"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
DEFAULT_WINDOW_DAYS = 60
PAGE_DELAY_SECONDS = 1.0
DEFAULT_PER_PAGE = 50
MAX_PAGES = 10  # safety cap; ~26 events / 60 days = 1 page in practice
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

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

# Defensive net: drop unconditionally if the title contains any of these,
# even when an included category matches. No live events currently trigger
# this — same guard as prospect_park.
_HARD_EXCLUDE_TITLE: tuple[str, ...] = (
    "21+",
    "adults only",
    "adults-only",
    "members only",
    "members-only",
)

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

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

_HTML_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def _strip_html(raw: str | None) -> str:
    """Strip HTML tags, decode common entities, collapse whitespace."""
    if not raw:
        return ""
    text = _HTML_TAG_RX.sub(" ", raw)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#8217;", "'")
        .replace("&#8211;", "–")
        .replace("&#8220;", '"')
        .replace("&#8221;", '"')
    )
    return _WS_RX.sub(" ", text).strip()


def _parse_utc_dt(raw: str | None) -> datetime | None:
    """Parse a UTC naive datetime string like '2026-06-14 13:30:00' into UTC-aware."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _parse_cost(cost: str | None) -> Price:
    """Map a Tribe cost string to a Price enum value."""
    if not cost:
        return Price.UNKNOWN
    cost_lower = cost.strip().lower()
    if "free" in cost_lower:
        return Price.FREE
    if "$" in cost:
        return Price.PAID
    # "Included with Museum admission" — admission itself is paid.
    if "admission" in cost_lower:
        return Price.PAID
    return Price.UNKNOWN


def _category_names(row: dict[str, Any]) -> set[str]:
    """Extract upstream category names from a Tribe row."""
    return {c.get("name", "") for c in (row.get("categories") or [])}


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Return True if the event passes the category-based kid-relevance filter.

    Exclusion categories and title hard-excludes win over the allowlist.
    """
    title = _strip_html(row.get("title")).lower()
    for kw in _HARD_EXCLUDE_TITLE:
        if kw in title:
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
        if tag not in tags and any(kw in title_lower for kw in keywords):
            tags.append(tag)
    return tags


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    if not _is_kid_relevant(row):
        return None

    title = _strip_html(row.get("title"))
    if not title:
        logger.debug("ny_transit_museum: skipping row with no title: id=%r", row.get("id"))
        return None

    start_dt = _parse_utc_dt(row.get("utc_start_date"))
    if start_dt is None:
        logger.debug("ny_transit_museum: skipping %r — no parseable start date", title)
        return None

    end_dt = _parse_utc_dt(row.get("utc_end_date"))

    # The Tribe id is per-occurrence on this site (recurring programs get a
    # distinct id + dated URL slug per occurrence) — verified live, see
    # module docstring. No date suffix needed.
    external_id = str(row["id"]) if row.get("id") else None
    url = row.get("url") or None

    excerpt_text = _strip_html(row.get("excerpt"))
    description_text = _strip_html(row.get("description"))
    description = excerpt_text or description_text or None
    if description and len(description) > 2000:
        description = description[:2000].rsplit(" ", 1)[0] + "…"

    price = _parse_cost(row.get("cost"))
    tags = _infer_tags(title, _category_names(row))
    venue_name, borough, lat, lng = _venue_fields(row)

    return Event(
        id=compute_id("ny_transit_museum", external_id=external_id, url=url, title=title),
        source="ny_transit_museum",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=venue_name,
        borough=borough,
        lat=lat,
        lng=lng,
        age_min=None,
        age_max=None,
        price=price,
        tags=tags,
        raw_payload=json.dumps(row, sort_keys=True, default=str),
    )


class NYTransitMuseumSource(Source):
    """New York Transit Museum events via the Tribe Events Calendar REST API."""

    name = "ny_transit_museum"

    def __init__(
        self,
        *,
        events_url: str = EVENTS_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        per_page: int = DEFAULT_PER_PAGE,
        page_delay: float = PAGE_DELAY_SECONDS,
        http_timeout: float = 30.0,
        max_pages: int = MAX_PAGES,
    ):
        self._events_url = events_url
        self._window_days = window_days
        self.window_days = window_days  # full-window re-fetch: missing-detection eligible
        self._per_page = per_page
        self._delay = page_delay
        self._timeout = http_timeout
        self._max_pages = max_pages

    def fetch(self) -> Iterable[Event]:
        """Paginate the Tribe REST API, yielding kid-relevant Events."""
        now = datetime.now(UTC)
        start_date = now.strftime("%Y-%m-%d %H:%M:%S")
        end_date = (now + timedelta(days=self._window_days)).strftime("%Y-%m-%d %H:%M:%S")

        total = 0
        page = 1
        while page <= self._max_pages:
            rows, next_url = self._get_page(page, start_date, end_date)
            if rows is None:
                # Hard error on this page — log already emitted in _get_page.
                break
            if not rows:
                break

            for row in rows:
                try:
                    ev = _parse_row(row)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "ny_transit_museum: failed to parse event id=%r",
                        row.get("id"),
                        exc_info=True,
                    )
                    continue
                if ev is not None:
                    total += 1
                    yield ev

            if not next_url:
                break

            page += 1
            time.sleep(self._delay)

        logger.info("ny_transit_museum: yielded %d events", total)

    def _get_page(
        self,
        page: int,
        start_date: str,
        end_date: str,
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Fetch one page of events.

        Returns (rows, next_rest_url) on success, or (None, None) on HTTP error.
        """
        params = {
            "per_page": self._per_page,
            "page": page,
            "start_date": start_date,
            "end_date": end_date,
            "status": "publish",
        }
        try:
            resp = cffi_requests.get(
                self._events_url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                impersonate="chrome",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = list(data.get("events") or [])
            next_url = data.get("next_rest_url") or None
            return rows, next_url
        except Exception:  # noqa: BLE001
            logger.warning("ny_transit_museum: failed to fetch page %d", page, exc_info=True)
            return None, None
