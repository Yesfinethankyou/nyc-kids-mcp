"""Brooklyn Public Library events.

BPL's public calendar (bklynlibrary.org/calendar) is a React SPA hosted at
discover.bklynlibrary.org and backed by a Solr-style search index. Events come
from that index, NOT from a Drupal REST API (an earlier draft of this source
assumed `/api/events`, which 404s — see memory `bpl-endpoint-is-wrong`).

Data flow:
  1. GET discover.bklynlibrary.org/api/search/index.php?event=true&view=grid
     &pagination=N — 1-based pages, 20 docs each, sorted by start date asc.
  2. Each doc is a flat Solr record with prefixed field names:
       ts_title / ts_body          -> title / description (body is HTML)
       ds_event_start_date         -> ISO8601 UTC start (Z-suffixed)
       ds_event_end_date           -> ISO8601 UTC end
       ss_event_location_master    -> branch name (the venue)
       ss_event_age                -> audience band (filter on this)
       sm_event_tags               -> BPL's own tag list
       item_id                     -> Drupal nid (our external_id)
       is_event_canceled / deleted / suppressed -> skip flags
  3. Filter to kid/teen/family audiences; adult-only bands are dropped.
  4. Yield Event objects. Detail URL is www.bklynlibrary.org/node/{item_id}.

Quirks:
  - The search endpoint is flaky: it intermittently returns HTTP 200 with an
    empty body. `_get_page` retries with backoff before giving up.
  - All BPL branches are in Brooklyn, so borough is always BROOKLYN.
  - Library programs are free; price defaults to FREE unless the body names a
    dollar amount.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

SEARCH_URL = "https://discover.bklynlibrary.org/api/search/index.php"
DRUPAL_BASE_URL = "https://www.bklynlibrary.org"
PAGE_DELAY_SECONDS = 1.0
# The endpoint flakes (empty 200s); retry each page this many times.
PAGE_RETRIES = 6
RETRY_DELAY_SECONDS = 2.0
# Safety cap so a runaway loop can't hammer the endpoint.
MAX_PAGES = 80
# How far ahead to ingest. Events are sorted ascending, so once we pass this
# horizon we stop paginating.
DEFAULT_WINDOW_DAYS = 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# ss_event_age bands BPL uses, mapped to (age_min, age_max). Anything not in
# here (e.g. "Adults", "Older Adults") is treated as adult-only and dropped.
_KID_AGE_BANDS: dict[str, tuple[int, int]] = {
    "birth to five years": (0, 5),
    "kids": (5, 12),
    "teens & young adults": (13, 18),
}
# Substrings that still signal a kid/family audience — used both for unknown
# ss_event_age labels and as a fallback over title/tags when the band is blank.
_KID_AGE_HINTS = (
    "kid", "teen", "child", "baby", "infant", "toddler", "preschool",
    "storytime", "story time", "lapsit", "youth", "family", "all ages",
    "birth to",
)
# Explicit adult-only bands (no kid signal).
_ADULT_AGES = {"adults", "older adults", "adult"}

# Kid-relevant keyword -> tag mapping (mirrors mommy_poppins / earlier draft).
_KID_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("story time", ("story time", "storytime", "story hour", "read aloud", "lapsit", "lap sit")),
    ("family", ("family", "families", "all ages", "intergenerational", "caregiver")),
    ("arts & crafts", ("craft", "paint", "draw", "collage", "diy", "art club", "art class")),
    ("nature", ("nature", "garden", "outdoor", "wildlife", "birding", "park", "botanical")),
    ("music", ("music", "concert", "sing", "dance", "drum", "movement")),
    ("educational", (
        "workshop", "stem", "science", "history", "homework", "tutoring", "class",
        "learning", "literacy", "robot", "coding", "esol",
    )),
    ("festival", ("festival", "fair", "block party", "celebration", "carnival", "party")),
    ("best for kids", (
        "kid", "child", "tot", "toddler", "preschool", "youth", "baby", "infant",
        "teen", "afterschool", "after school",
    )),
    ("movie", ("movie", "film", "screening", "cinema")),
    ("theater", ("theater", "theatre", "puppet", "play")),
    ("gaming", ("gaming", "game", "tournament", "esports", "video game")),
]

_HTML_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")
_DOLLAR_RX = re.compile(r"\$\d")


# --- Pure helper functions (testable without network) ------------------------


def _strip_html(raw: str | None) -> str:
    """Strip HTML tags and collapse whitespace from a body field."""
    if not raw:
        return ""
    text = _HTML_TAG_RX.sub(" ", raw)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _WS_RX.sub(" ", text).strip()


def _parse_dt(raw: str | None) -> datetime | None:
    """Parse an ISO8601 datetime (incl. trailing Z) to a UTC-aware datetime."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _age_band(age_label: str | None) -> tuple[int | None, int | None]:
    """Map a ss_event_age label to an (age_min, age_max) range."""
    if not age_label:
        return None, None
    key = age_label.strip().lower()
    if key in _KID_AGE_BANDS:
        return _KID_AGE_BANDS[key]
    if "birth to" in key or "all ages" in key:
        return 0, 5 if "five" in key else 99
    return None, None


def _is_kid_relevant(doc: dict[str, Any]) -> bool:
    """Return True if the event targets children, teens, or families."""
    age = (doc.get("ss_event_age") or "").strip().lower()
    if age:
        if age in _ADULT_AGES:
            return False
        if age in _KID_AGE_BANDS or any(h in age for h in _KID_AGE_HINTS):
            return True
        # Unknown non-adult band: fall through to keyword check.
    # No usable age band — look for kid signals in title/tags.
    haystack = (doc.get("ts_title") or "").lower()
    haystack += " " + " ".join(str(t).lower() for t in (doc.get("sm_event_tags") or []))
    return any(h in haystack for h in _KID_AGE_HINTS)


def _extract_price(body_text: str) -> Price:
    """BPL programs are free; flag PAID only if the body names a dollar amount."""
    if _DOLLAR_RX.search(body_text):
        return Price.PAID
    return Price.FREE


def _normalize_tags(doc: dict[str, Any], title: str, body_text: str) -> list[str]:
    """Combine BPL's own tags with inferred kid-keyword tags, deduped."""
    tags: list[str] = []
    for raw in doc.get("sm_event_tags") or []:
        t = str(raw).strip().lower()
        if t and t not in tags:
            tags.append(t)
    haystack = (title + " " + body_text).lower()
    for tag, keywords in _KID_KEYWORDS:
        if tag not in tags and any(kw in haystack for kw in keywords):
            tags.append(tag)
    return tags


def _parse_row(doc: dict[str, Any]) -> Event | None:
    """Parse one Solr event doc into an Event, or None if it should be skipped."""
    if doc.get("deleted") or doc.get("suppressed") or doc.get("is_event_canceled"):
        return None
    if not _is_kid_relevant(doc):
        return None

    title = (doc.get("ts_title") or "").strip()
    if not title:
        logger.debug("bpl: skipping doc with no title: %r", doc.get("item_id"))
        return None

    start_dt = _parse_dt(doc.get("ds_event_start_date"))
    if start_dt is None:
        epoch = doc.get("is_event_start_date")
        if isinstance(epoch, int):
            start_dt = datetime.fromtimestamp(epoch, tz=UTC)
    if start_dt is None:
        logger.debug("bpl: skipping %r — no parseable start date", title)
        return None

    end_dt = _parse_dt(doc.get("ds_event_end_date"))

    item_id = str(doc.get("item_id") or "").strip()
    external_id = item_id or None
    url = f"{DRUPAL_BASE_URL}/node/{item_id}" if item_id else None

    body_text = _strip_html(doc.get("ts_body"))
    description = body_text or None

    venue_name = (doc.get("ss_event_location_master") or "").strip() or None
    if not venue_name:
        venue_name = (doc.get("ss_event_location") or "").strip() or None

    age_min, age_max = _age_band(doc.get("ss_event_age"))
    price = _extract_price(body_text)
    tags = _normalize_tags(doc, title, body_text)

    return Event(
        id=compute_id("bpl", external_id=external_id, url=url, title=title, venue=venue_name),
        source="bpl",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=venue_name,
        borough=Borough.BROOKLYN,
        lat=None,
        lng=None,
        age_min=age_min,
        age_max=age_max,
        price=price,
        tags=tags,
        raw_payload=json.dumps(doc, sort_keys=True, default=str),
    )


class BPLSource(Source):
    """Brooklyn Public Library kid/family events via the discover search index."""

    name = "bpl"

    def __init__(
        self,
        *,
        search_url: str = SEARCH_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        page_delay: float = PAGE_DELAY_SECONDS,
        http_timeout: float = 30.0,
        max_pages: int = MAX_PAGES,
    ):
        self._search_url = search_url
        self._window_days = window_days
        self._page_delay = page_delay
        self._timeout = http_timeout
        self._max_pages = max_pages

    def fetch(self) -> Iterable[Event]:
        """Paginate the discover search index, yielding kid/family Events."""
        horizon = datetime.now(UTC) + timedelta(days=self._window_days)
        count = 0
        for page in range(1, self._max_pages + 1):
            docs = self._get_page(page)
            if not docs:
                break

            past_horizon = False
            for doc in docs:
                start_dt = _parse_dt(doc.get("ds_event_start_date"))
                if start_dt and start_dt > horizon:
                    past_horizon = True
                    continue
                try:
                    ev = _parse_row(doc)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "bpl: failed to parse doc item_id=%r", doc.get("item_id"), exc_info=True
                    )
                    continue
                if ev is not None:
                    count += 1
                    yield ev

            # Results are sorted by start date ascending, so once a full page
            # is beyond the horizon there is nothing left worth fetching.
            if past_horizon and all(
                (_parse_dt(d.get("ds_event_start_date")) or horizon) > horizon for d in docs
            ):
                break

            time.sleep(self._page_delay)

        logger.info("bpl: yielded %d kid/family events", count)

    def _get_page(self, page: int) -> list[dict[str, Any]]:
        """Fetch one results page, retrying through the endpoint's empty 200s."""
        params = {"event": "true", "view": "grid", "pagination": page}
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://discover.bklynlibrary.org/?event=true",
            "X-Requested-With": "XMLHttpRequest",
        }
        for attempt in range(PAGE_RETRIES):
            try:
                resp = httpx.get(
                    self._search_url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                    follow_redirects=True,
                )
                resp.raise_for_status()
                if resp.text.strip():
                    data = resp.json()
                    return list((data.get("response") or {}).get("docs") or [])
            except (httpx.HTTPError, json.JSONDecodeError):
                logger.debug("bpl: page %d attempt %d failed", page, attempt, exc_info=True)
            time.sleep(RETRY_DELAY_SECONDS)
        logger.warning("bpl: gave up on page %d after %d attempts", page, PAGE_RETRIES)
        return []
