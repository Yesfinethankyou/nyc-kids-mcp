"""Governors Island events.

Governors Island (a 172-acre former military base in New York Harbor, a short
ferry from Lower Manhattan; administratively part of the Borough of Manhattan)
is a seasonal public park and a heavily family-oriented destination. Its
calendar mixes festivals, concerts, hands-on workshops, playgrounds, walking
tours, and a large slate of arts programming with a thinner layer of
adult-skewing items (fundraiser galas, 7AM road races) and non-event amenities
(bike rentals, a spa, the free digital guide).

Data flow:
  1. GET /things-to-do.json (curl_cffi with Chrome impersonation — plain
     fetchers get a bot-block HTML page even on /wp-json paths).
  2. The endpoint is a custom Craft CMS / Solspace-Calendar controller, NOT
     WordPress + Tribe (the prior backlog "no API surface" verdict was a
     non-impersonating-probe artifact — there IS a clean JSON feed). It returns
     `{"data": [...], "meta": {...}}`; each row is one event entry carrying its
     NEXT upcoming occurrence (`meta.criteria` =
     `loadOccurrences:next, rangeStart:now, orderBy:id asc`).
  3. Filter: Governors Island skews family, so this source is INCLUSIVE by
     default and only drops a blocklist of clearly-adult content and non-event
     amenities (see `_is_kid_relevant`). No allowlist — that would wrongly drop
     keyword-less kid gold like "Slide Hill" / "Hammock Grove Play Area".
  4. Strip HTML, hardcode venue / borough, yield Events.

Quirks (verified live + against the captured fixture, 2026-06-20):
  - **Dates are "floating" local wall-time mislabeled as UTC.** `startDate`
    reads e.g. `2026-07-25T12:00:00.000000Z`, but the event's `openTimeText`
    is "12-5PM" and the calendar's `icsTimezone` is "floating" — so the value
    is 12:00 NOON *local* New York time, not UTC. We strip the bogus `Z`, parse
    naive, and attach `America/New_York`. Treating it as UTC would shift every
    event by ~4-5 hours.
  - **The `id` is per-event and unique** (100 distinct ids in a 100-row live
    fetch). Recurring events surface only their next occurrence under one id,
    so `external_id = str(id)` is correct — no `:start.isoformat()` suffix.
    A recurring event's `start_dt` advances each run; compute_id excludes
    start_dt, so the row updates in place rather than duplicating.
  - **The feed hard-caps at 100 rows ordered `id asc`** — no pagination param
    works (`?limit`/`?per_page`/`?page`/`?offset` are all ignored). Because the
    cap is on id-ascending order, if the island ever has >100 active listings
    the highest-id (newest-created) ones silently fall off the end. A fetch is
    therefore NOT a guaranteed full re-fetch of the window, so this source
    stays OUT of missing-event/cancellation detection (`window_days = None`,
    same caution as mommy_poppins). Opting in would risk falsely flagging real
    events that merely scrolled past the cap.
  - No `cost` field upstream → price is `UNKNOWN` for every row. No per-event
    lat/lng and no age range. `venue`/borough are hardcoded ("Governors
    Island" / Manhattan); the row's `locations[].locationName` (specific spots
    like "Nolan Park - Building 15") is preserved only in `raw_payload`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from curl_cffi import requests as cffi_requests

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, MEMBERS_ONLY, contains_any
from .base import Source

logger = logging.getLogger(__name__)

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://www.govisland.com"
EVENTS_URL = f"{BASE_URL}/things-to-do.json"
VENUE_NAME = "Governors Island"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# ---------------------------------------------------------------------------
# Kid-relevance filter (inclusive + blocklist)
#
# Governors Island is a family destination, so we INCLUDE by default and drop
# only a focused blocklist. An allowlist would be wrong here: obvious kid items
# ("Slide Hill", "Hammock Grove Play Area") carry no kid keyword at all.
# ---------------------------------------------------------------------------

# Strong adult signals (`ADULT_BLOCKLIST`) are checked against title + body.
# Venue-specific adult-skewing / non-event terms are checked against the TITLE
# only, to avoid dropping a family festival whose body merely mentions a "wine
# garden" etc. Covers fundraiser galas, beach clubs, after-parties, open-bar
# events, and non-event amenities (bike rentals, the spa "QC NY", the digital
# guide). Alcohol-tasting terms (cocktail / wine or beer tasting / happy hour)
# were intentionally removed — alcohol alone isn't an adult-only signal here.
# Hyphen/space variants ("after-party") are handled by the shared normalizer.
_TITLE_EXCLUDE: tuple[str, ...] = (
    "gala",
    "beach club",
    "after party",
    "open bar",
    "bike rental",
    "citi bike",
    "digital guide",
    "qc ny",
)

# Competitive road races (NYCRUNS 5K/10K/marathon series) — 7AM athletic events,
# not kid programming. Matches "5K"/"10K"/"marathon"/"half marathon"/"nycruns".
_RACE_RX = re.compile(r"\bnycruns\b|\bhalf marathon\b|\bmarathon\b|\b\d+\s?k\b", re.I)


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    """Return True unless the row hits the adult/non-event blocklist."""
    title = _strip_html(row.get("title")).lower()
    body = _strip_html(row.get("body")).lower()
    haystack = f"{title} {body}"

    if contains_any(haystack, ADULT_BLOCKLIST):
        return False
    if contains_any(title, _TITLE_EXCLUDE) or contains_any(title, MEMBERS_ONLY):
        return False
    if _RACE_RX.search(title):
        return False
    return True


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------

_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    # NB: "family"/"families" are deliberately NOT here — funder boilerplate in
    # the body ("Donald R. Mullen Family Foundation" etc.) appears on many art
    # listings and would over-tag them. The always-on base "family" tag already
    # covers family-friendliness; this tag wants a real kid-specific signal.
    ("best for kids", ("kids", "children", "all ages", "all-ages", "toddler",
                       "playground", "play area", "play lawn")),
    ("arts and crafts", ("workshop", "craft", "make your own", "sculpture",
                         "painting", "cyanotype", "open studio", "studios",
                         "drawing", "printmaking", "mask")),
    ("music", ("jazz", "concert", "music", "stomp", "band", "sounds")),
    ("outdoors", ("garden", "hill", "field", "orchard", "hammock", "oyster",
                  "nature", "farm", "lawn", "forest", "tree", "compost")),
    ("tour", ("tour", "walk")),
    ("festival", ("festival", "fair", "market", "fest")),
    ("history", ("fort", "historic", "history", "castle")),
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
        .replace("&#038;", "&")
        .replace("&#8217;", "'")
        .replace("&#8211;", "–")
        .replace("&#8212;", "—")
        .replace("&ndash;", "–")
        .replace("&mdash;", "—")
        .replace("&thinsp;", " ")
        .replace("&#8220;", '"')
        .replace("&#8221;", '"')
    )
    return _WS_RX.sub(" ", text).strip()


def _parse_floating_dt(raw: str | None) -> datetime | None:
    """Parse a floating-local ISO string into an America/New_York-aware datetime.

    Upstream stamps a bogus trailing ``Z`` on what is really local wall-time
    (the calendar's icsTimezone is "floating"). We drop the ``Z`` and attach
    America/New_York so the stored value is tz-aware and correct.
    """
    if not raw:
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1]
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=NYC_TZ)


def _infer_tags(title: str, body: str | None) -> list[str]:
    """Infer tags from title + body keywords. Always includes 'family'."""
    haystack = title.lower() + " " + (body or "").lower()
    tags: list[str] = ["family"]
    for tag, keywords in _TAG_RULES:
        # Leading word boundary stops short keywords from matching mid-word:
        # "tree"≠"street", "hill"≠"Churchill", "fort"≠"comfort", "walk"≠
        # "boardwalk" — while still matching prefixes ("tree" → "trees").
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", haystack) for kw in keywords
        ):
            tags.append(tag)
    return tags


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one things-to-do record into an Event, or None if filtered out."""
    if not _is_kid_relevant(row):
        return None

    title = _strip_html(row.get("title"))
    if not title:
        logger.debug("governors_island: skipping row with no title: id=%r", row.get("id"))
        return None

    start_dt = _parse_floating_dt(row.get("startDate"))
    if start_dt is None:
        logger.debug("governors_island: skipping %r — no parseable start date", title)
        return None

    end_dt = _parse_floating_dt(row.get("endDate"))

    external_id = str(row["id"]) if row.get("id") else None
    url = row.get("url") or None

    body_text = _strip_html(row.get("body"))
    description = body_text or None
    if description and len(description) > 2000:
        description = description[:2000].rsplit(" ", 1)[0] + "…"

    tags = _infer_tags(title, body_text)

    return Event(
        id=compute_id("governors_island", external_id=external_id, url=url, title=title),
        source="governors_island",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=VENUE_NAME,
        borough=Borough.MANHATTAN,  # Governors Island is part of Manhattan
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=Price.UNKNOWN,  # no cost field upstream
        tags=tags,
        raw_payload=json.dumps(row, sort_keys=True, default=str),
    )


class GovernorsIslandSource(Source):
    """Governors Island things-to-do via the site's Craft CMS JSON feed."""

    name = "governors_island"

    # window_days intentionally left as inherited None: the feed hard-caps at
    # 100 rows ordered id-asc with no pagination, so a fetch is not a
    # guaranteed full window re-fetch — opting into missing-detection would risk
    # falsely flagging events that merely scrolled past the cap. See docstring.

    def __init__(
        self,
        *,
        events_url: str = EVENTS_URL,
        http_timeout: float = 45.0,
    ):
        self._events_url = events_url
        self._timeout = http_timeout

    def fetch(self) -> Iterable[Event]:
        """Fetch the things-to-do feed, yielding kid-relevant Events."""
        rows = self._get_rows()
        if rows is None:
            # Hard failure already logged; ingest skips this source's rows.
            return

        total = 0
        for row in rows:
            try:
                ev = _parse_row(row)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "governors_island: failed to parse event id=%r",
                    row.get("id"),
                    exc_info=True,
                )
                continue
            if ev is not None:
                total += 1
                yield ev

        logger.info("governors_island: yielded %d events", total)

    def _get_rows(self) -> list[dict[str, Any]] | None:
        """Fetch the feed. Returns the row list, or None on HTTP/parse error."""
        try:
            resp = cffi_requests.get(
                self._events_url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                impersonate="chrome",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return list(data.get("data") or [])
        except Exception:  # noqa: BLE001
            logger.warning("governors_island: failed to fetch feed", exc_info=True)
            return None
