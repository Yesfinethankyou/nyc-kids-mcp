"""Industry City events.

Industry City (a 35-acre former manufacturing complex on the Sunset Park,
Brooklyn waterfront) hosts a busy public events calendar: maker/craft
workshops, family programming (e.g. the Puppetworks marionette theatre),
food-hall pop-ups, ticketed culinary tours, outdoor watch parties, plus a
fair amount of adult nightlife (21+ shows, drink tastings). Events come from a
WordPress / The Events Calendar (Tribe) REST API — the fourth Tribe instance
in this project (after Green-Wood Cemetery, Prospect Park, and NY Transit
Museum); this module is a copy-adapt of those.

Data flow:
  1. GET /wp-json/tribe/events/v1/events?per_page=50&page=N (curl_cffi with
     Chrome impersonation — the Streetsense theme bot-blocks plain fetchers).
  2. Paginate via `next_rest_url` in each response until absent (~195 events
     / 60 days → ~4 pages at per_page=50).
  3. Filter for kid-relevance on title/description KEYWORDS (categories here
     are NOT kid-curated — see below), with `Nightlife` as a hard-exclude
     category and an adult-content title/description blocklist.
  4. Strip HTML, hardcode venue / borough, yield Events.

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

import json
import logging
import os
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from curl_cffi import requests as cffi_requests

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, ADULT_TITLE_BLOCKLIST, contains_any
from .base import Source

logger = logging.getLogger(__name__)

BASE_URL = "https://industrycity.com"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
VENUE_NAME = "Industry City"
DEFAULT_WINDOW_DAYS = 60
PAGE_DELAY_SECONDS = 1.0
DEFAULT_PER_PAGE = 50
MAX_PAGES = 30  # safety cap; ~195 events / 50 per page = 4 pages in practice
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

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
        .replace("&#038;", "&")
        .replace("&#8220;", '"')
        .replace("&#8221;", '"')
    )
    return _WS_RX.sub(" ", text).strip()


def _parse_utc_dt(raw: str | None) -> datetime | None:
    """Parse a UTC naive datetime string like '2026-06-20 18:00:00' into UTC-aware."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _category_names(row: dict[str, Any]) -> set[str]:
    """Extract upstream category names from a Tribe row."""
    return {c.get("name", "") for c in (row.get("categories") or [])}


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


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    if not _is_kid_relevant(row):
        return None

    title = _strip_html(row.get("title"))
    if not title:
        logger.debug("industry_city: skipping row with no title: id=%r", row.get("id"))
        return None

    start_dt = _parse_utc_dt(row.get("utc_start_date"))
    if start_dt is None:
        logger.debug("industry_city: skipping %r — no parseable start date", title)
        return None

    end_dt = _parse_utc_dt(row.get("utc_end_date"))

    # The Tribe id is per-occurrence on this site (recurring events get a
    # distinct id + dated URL slug per occurrence) — verified against the
    # fixture, see module docstring. No date suffix needed.
    external_id = str(row["id"]) if row.get("id") else None
    url = row.get("url") or None

    excerpt_text = _strip_html(row.get("excerpt"))
    description_text = _strip_html(row.get("description"))
    description = excerpt_text or description_text or None
    if description and len(description) > 2000:
        description = description[:2000].rsplit(" ", 1)[0] + "…"

    tags = _infer_tags(title, description_text)

    return Event(
        id=compute_id("industry_city", external_id=external_id, url=url, title=title),
        source="industry_city",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=VENUE_NAME,
        borough=Borough.BROOKLYN,
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=Price.UNKNOWN,  # cost is always empty upstream
        tags=tags,
        raw_payload=json.dumps(row, sort_keys=True, default=str),
    )


class IndustryCitySource(Source):
    """Industry City events via the Tribe Events Calendar REST API."""

    name = "industry_city"

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
                        "industry_city: failed to parse event id=%r",
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

        logger.info("industry_city: yielded %d events", total)

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
                impersonate="safari",
                timeout=self._timeout,
                proxy=os.environ.get("HTTPS_PROXY"),
                verify=os.environ.get("SSL_CERT_FILE") or True,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = list(data.get("events") or [])
            next_url = data.get("next_rest_url") or None
            return rows, next_url
        except Exception:  # noqa: BLE001
            logger.warning("industry_city: failed to fetch page %d", page, exc_info=True)
            return None, None
