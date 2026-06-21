"""Prospect Park Alliance events.

The Prospect Park Alliance publishes the full park calendar — nature
programming (Audubon Center, Pop-Up Audubon), the Carousel, Lefferts Historic
House, films, performances, plus plenty of non-kid programming (yoga,
greenmarkets, fun runs). Events come from a WordPress / Tribe Events Calendar
REST API, the same plugin Green-Wood Cemetery uses.

Data flow:
  1. GET /wp-json/tribe/events/v1/events?per_page=50&page=N (curl_cffi with
     Chrome impersonation — Cloudflare blocks plain fetchers).
  2. Paginate via `next_rest_url` in each response until absent.
  3. Filter by upstream `categories` against a kid-relevant allowlist.
  4. Strip HTML from description; map the cost string to a Price enum.
  5. Yield Event objects.

Quirks (verified live, 2026-06):
  - The Tribe `id` IS per-occurrence: recurring events ("Carousel Rides",
    "Nature Exploration") get a distinct id and a dated URL slug per
    occurrence, so `external_id = str(id)` is safe — no `:start.isoformat()`
    suffix needed (456 events in a 60-day window → 456 distinct ids/urls).
  - `venue` is always an empty list upstream. Every event is in Prospect
    Park; venue and borough are hardcoded.
  - `cost` is free text: "Free", "Free, RSVP!", "FREE, RSVP", "$3 – $13",
    "Prices Vary", or empty.
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
  - Unlike Green-Wood's keyword filter, this source has reliable upstream
    categories, so filtering is category-driven. A small title hard-exclude
    list is kept as a defensive net (none currently trigger in live data).
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

BASE_URL = "https://www.prospectpark.org"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
VENUE_NAME = "Prospect Park"
DEFAULT_WINDOW_DAYS = 60
PAGE_DELAY_SECONDS = 1.0
DEFAULT_PER_PAGE = 50
MAX_PAGES = 30  # safety cap; ~456 events / 50 per page = 10 pages in practice
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

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

# Defensive net: drop unconditionally if the title contains any of these,
# even when an included category matches. No live events currently trigger
# this — it guards against adult programming slipping into broad categories
# like "Performing Arts" or "Film".
_HARD_EXCLUDE_TITLE: tuple[str, ...] = (
    "21+",
    "adults only",
    "adults-only",
    "members only",
    "members-only",
)

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
    """Parse a UTC naive datetime string like '2026-06-14 14:00:00' into UTC-aware."""
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
    return Price.UNKNOWN


def _category_names(row: dict[str, Any]) -> set[str]:
    """Extract upstream category names from a Tribe row."""
    return {c.get("name", "") for c in (row.get("categories") or [])}


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Return True if the event passes the category-based kid-relevance filter."""
    title = _strip_html(row.get("title")).lower()
    for kw in _HARD_EXCLUDE_TITLE:
        if kw in title:
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


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    if not _is_kid_relevant(row):
        return None

    title = _strip_html(row.get("title"))
    if not title:
        logger.debug("prospect_park: skipping row with no title: id=%r", row.get("id"))
        return None

    start_dt = _parse_utc_dt(row.get("utc_start_date"))
    if start_dt is None:
        logger.debug("prospect_park: skipping %r — no parseable start date", title)
        return None

    end_dt = _parse_utc_dt(row.get("utc_end_date"))

    # The Tribe id is per-occurrence on this site (recurring events get a
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

    return Event(
        id=compute_id("prospect_park", external_id=external_id, url=url, title=title),
        source="prospect_park",
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
        price=price,
        tags=tags,
        raw_payload=json.dumps(row, sort_keys=True, default=str),
    )


class ProspectParkSource(Source):
    """Prospect Park Alliance events via the Tribe Events Calendar REST API."""

    name = "prospect_park"

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
                        "prospect_park: failed to parse event id=%r",
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

        logger.info("prospect_park: yielded %d events", total)

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
            logger.warning("prospect_park: failed to fetch page %d", page, exc_info=True)
            return None, None
