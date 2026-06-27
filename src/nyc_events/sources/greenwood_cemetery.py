"""Green-Wood Cemetery events.

Green-Wood Cemetery runs a year-round public events programme: walking tours,
nature talks, film screenings, concerts, seasonal festivals, and family
programming. Events come from a WordPress / Tribe Events Calendar REST API.

Data flow:
  1. GET /wp-json/tribe/events/v1/events?per_page=50&page=N
  2. Paginate via `next_rest_url` in each response until absent.
  3. Each event in `events[]` is a fully structured Tribe record with UTC
     start/end dates, HTML description, cost string, categories, etc.
  4. Filter by kid-relevance allowlist/blocklist against title + excerpt.
  5. Strip HTML tags from description; map cost string to Price enum.
  6. Yield Event objects.

Quirks:
  - The `description` field is HTML including inline <style> blocks — strip
    via regex. The `excerpt` field (when present) is a cleaner plain summary;
    we use description stripped + excerpted as fallback.
  - The `cost` field is a free-text string ("Free", "$30 / $24 members", "").
  - The API returns `utc_start_date` and `utc_end_date` — use those directly
    instead of converting the local strings.
  - Borough is always Brooklyn; venue is hardcoded.
  - Not all events are kid-appropriate (adult after-hours tours, galas). A
    keyword allowlist gates inclusion; a blocklist gates exclusion.
"""

from __future__ import annotations

import html
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
from ._filters import (
    ADULT_BLOCKLIST,
    ADULT_TITLE_BLOCKLIST,
    MEMBERS_ONLY,
    contains_any,
)
from .base import Source

logger = logging.getLogger(__name__)

BASE_URL = "https://www.green-wood.com"
EVENTS_URL = f"{BASE_URL}/wp-json/tribe/events/v1/events"
VENUE_NAME = "Green-Wood Cemetery"
VENUE_ADDRESS = "500 25th Street, Brooklyn, NY 11232"
DEFAULT_WINDOW_DAYS = 60
PAGE_DELAY_SECONDS = 1.0
DEFAULT_PER_PAGE = 50
MAX_PAGES = 60  # safety cap; ~200 events / 50 per page = 4 pages in practice
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

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

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

_HTML_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def _strip_html(raw: str | None) -> str:
    """Strip HTML tags, collapse whitespace."""
    if not raw:
        return ""
    text = _HTML_TAG_RX.sub(" ", raw)
    text = html.unescape(text).replace("\xa0", " ")
    return _WS_RX.sub(" ", text).strip()


def _parse_utc_dt(raw: str | None) -> datetime | None:
    """Parse a UTC naive datetime string like '2026-06-05 23:30:00' into UTC-aware."""
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


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one Tribe event record into an Event, or None if filtered out."""
    if not _is_kid_relevant(row):
        return None

    title = _strip_html(row.get("title") or "").strip()
    if not title:
        logger.debug("greenwood_cemetery: skipping row with no title: id=%r", row.get("id"))
        return None

    start_dt = _parse_utc_dt(row.get("utc_start_date"))
    if start_dt is None:
        logger.debug("greenwood_cemetery: skipping %r — no parseable start date", title)
        return None

    end_dt = _parse_utc_dt(row.get("utc_end_date"))

    external_id = str(row["id"]) if row.get("id") else None
    url = row.get("url") or None

    # Use excerpt if available (cleaner); fall back to stripped description.
    excerpt_text = _strip_html(row.get("excerpt"))
    description_text = _strip_html(row.get("description"))
    description = excerpt_text or description_text or None
    # Trim purely-CSS noise that bleeds through the regex strip.
    if description and len(description) > 2000:
        description = description[:2000].rsplit(" ", 1)[0] + "…"

    price = _parse_cost(row.get("cost"))
    tags = _infer_tags(title, description)

    return Event(
        id=compute_id("greenwood_cemetery", external_id=external_id, url=url, title=title),
        source="greenwood_cemetery",
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


class GreenWoodCemeterySource(Source):
    """Green-Wood Cemetery public events via the Tribe Events Calendar REST API."""

    name = "greenwood_cemetery"

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
                        "greenwood_cemetery: failed to parse event id=%r",
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

        logger.info("greenwood_cemetery: yielded %d events", total)

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
            logger.warning(
                "greenwood_cemetery: failed to fetch page %d", page, exc_info=True
            )
            return None, None
