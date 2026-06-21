"""Domino Park events.

Domino Park is a 6-acre waterfront public park on the Williamsburg, Brooklyn
shoreline (built on the old Domino Sugar Refinery site). Its programming is a
curated, overwhelmingly family-friendly calendar: greenmarkets, kids' play
sessions, yoga/fitness, craft nights, outdoor movies, dance and music. It is
NOT a permit registry, so this source is INCLUSIVE — like Governors Island,
it includes by default and only drops a light blocklist of clearly-adult
content (see `_is_kid_relevant`).

Data flow:
  1. The site is a Next.js (App Router) front-end backed by Sanity CMS. The
     prior backlog "Sanity headless, no public feed" verdict was a
     non-impersonating-probe artifact: the `production` dataset on project
     `4shd8slw` allows anonymous reads, so we query the public GROQ API
     directly — no HTML scraping, no headless browser.
  2. GET {APICDN}/data/query/{dataset}?query=*[_type=="event"]{...}
     (curl_cffi with Chrome impersonation — the apex domain bot-blocks plain
     fetchers; the Sanity CDN is more permissive but we impersonate anyway).
  3. Expand recurring events into per-occurrence rows, filter, yield Events.

Recurrence model (verified live against 125 docs, 2026-06-20):
  - `variant` is the AUTHORITATIVE recurrence switch, NOT `frequency`:
      * "reoccurring"  → a single doc representing a series; expand it using
                         `frequency` (weekly/monthly/daily) + `interval`
                         (every-N), bounded by `startDate`..`endDate`.
      * "single-day"   → exactly ONE event on `startDate`. These docs OFTEN
                         carry leftover `frequency`/`interval`/`endDate` from a
                         template (e.g. "Longevity Stick" exists as several
                         single-day docs, some with endDate < startDate) — that
                         data is VESTIGIAL and must be ignored, or we'd both
                         double-count and emit garbage dates.
      * "multi-day"    → ONE event spanning `startDate`..`endDate`.
  - Each occurrence of a "reoccurring" series gets
    `external_id = f"{_id}:{date}"` (the permit-source precedent); single- and
    multi-day docs use the bare Sanity `_id` (unique per doc). The two
    representations don't overlap upstream, so no double-counting.
  - `startHour`/`endHour` are free-text ("6 pm", "10:00 AM", "7:30 pm ",
    "8:00am") parsed leniently; unparseable → midnight (date-only). Times are
    local wall-clock → America/New_York.
  - Rich fields: `description` (plain text), `latitude`/`longitude` (populated
    on ~98% of docs), `tags` (category labels), `slug` (→ event URL). No price
    field → UNKNOWN. Venue/borough hardcoded "Domino Park" / Brooklyn; the
    per-event `location` (a spot within the park) is kept in `raw_payload`.
  - `window_days = 60`: the GROQ query returns the full event collection every
    run and occurrence ids are deterministic, so a fetch is a true full-window
    re-fetch → opted into missing-event/cancellation detection.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from curl_cffi import requests as cffi_requests

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, ADULT_TITLE_BLOCKLIST, contains_any
from .base import Source

logger = logging.getLogger(__name__)

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://www.dominopark.com"
SANITY_PROJECT = "4shd8slw"
SANITY_DATASET = "production"
SANITY_API_VERSION = "v2021-10-21"
APICDN = f"https://{SANITY_PROJECT}.apicdn.sanity.io/{SANITY_API_VERSION}/data/query/{SANITY_DATASET}"
# Pull only the fields we use; keeps the payload (and raw_payload) lean.
GROQ_QUERY = (
    '*[_type=="event"]{_id,_type,title,slug,description,startDate,endDate,'
    'startHour,endHour,variant,frequency,interval,tags,location,latitude,longitude}'
)
VENUE_NAME = "Domino Park"
DEFAULT_WINDOW_DAYS = 60
MAX_OCCURRENCES = 200  # safety cap on recurrence expansion per event
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# ---------------------------------------------------------------------------
# Kid-relevance filter (inclusive + blocklist)
#
# Domino Park's calendar is a curated family-park program (tags are dominated
# by "Family & Education"), so we INCLUDE by default and drop only the shared
# adult blocklist (a safety net, consistent with the Governors Island source) —
# checked against title + description. Bare "drag" is deliberately NOT
# blocklisted (would catch family throwback/skate nights); only the shared
# "drag show"/"drag brunch". Alcohol-tasting terms are intentionally absent —
# alcohol alone isn't an adult-only signal at a family park.
# ---------------------------------------------------------------------------


def _is_kid_relevant(doc: dict[str, Any]) -> bool:
    """Return True unless the doc hits the adult blocklist."""
    title = doc.get("title") or ""
    haystack = f"{title} {doc.get('description') or ''}"
    return not (
        contains_any(haystack, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
    )


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------

# Sanity category label -> our tag.
_CATEGORY_TAGS: dict[str, str] = {
    "Family & Education": "best for kids",
    "Music, Dance & Theater": "music",
    "Movies in the Square": "movies",
    "Arts, Creativity & Community": "arts and crafts",
    "Movement & Wellness": "wellness",
    "Sugar-Sugar": "music",
}

_KEYWORD_TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("market", ("market", "greenmarket", "kiosk", "flowers", "pop-up", "vendor")),
    ("outdoors", ("garden", "field", "lawn", "yoga", "fitness", "croquet",
                  "compost", "horticulture", "nature", "waterfront")),
    # NB: no bare "art" — it matches substrings like "st-art"/"p-art"; real art
    # events are already tagged via the "Arts, Creativity & Community" category.
    ("arts and crafts", ("craft", "workshop", "painting", "drawing", "pottery")),
    ("storytelling", ("storytime", "story time", "storytelling", "moth")),
    ("movies", ("movie", "film", "screening")),
    ("music", ("music", "dj", "dance", "salsa", "concert", "jazz")),
]


def _infer_tags(title: str, description: str | None, categories: list[str] | None) -> list[str]:
    """Infer tags from Sanity categories + title/description keywords."""
    tags: list[str] = ["family"]
    for cat in categories or []:
        tag = _CATEGORY_TAGS.get(cat)
        if tag and tag not in tags:
            tags.append(tag)
    haystack = title.lower() + " " + (description or "").lower()
    for tag, keywords in _KEYWORD_TAGS:
        # Leading word boundary stops short keywords from matching mid-word:
        # "moth" (storytelling) no longer hits "mother", "dj" no longer hits
        # "adjust"; prefixes still match.
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", haystack) for kw in keywords
        ):
            tags.append(tag)
    return tags


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

_WS_RX = re.compile(r"\s+")
_HOUR_RX = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.I)


def _clean_text(raw: str | None) -> str:
    """Collapse whitespace and trim."""
    if not raw:
        return ""
    return _WS_RX.sub(" ", raw).strip()


def _parse_date(raw: str | None) -> date | None:
    """Parse a 'YYYY-MM-DD' date string."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except (ValueError, TypeError):
        return None


def _parse_hour(raw: str | None) -> tuple[int, int] | None:
    """Parse a free-text hour like '6 pm', '10:00 AM', '7:30 pm ' -> (h, m), 24h.

    Returns None when no am/pm time is found (caller falls back to midnight).
    """
    if not raw:
        return None
    m = _HOUR_RX.search(raw.strip())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = m.group(3).lower()
    if hour == 12:
        hour = 0
    if meridiem == "p":
        hour += 12
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return hour, minute


def _combine(d: date, hour: tuple[int, int] | None) -> datetime:
    """Combine a date with an optional (hour, minute) into an NY-aware datetime."""
    h, mi = hour if hour else (0, 0)
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=NYC_TZ)


def _add_months(d: date, n: int) -> date:
    """Add n months, clamping the day to the target month's length."""
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _occurrence_dates(
    start: date,
    end: date | None,
    frequency: str | None,
    interval: int | None,
    win_start: date,
    win_end: date,
) -> list[date]:
    """Expand a recurrence rule into occurrence dates within the window.

    Bounded by [max(start, win_start), min(end or win_end, win_end)].
    """
    step_interval = interval if (interval and interval >= 1) else 1
    hi = min(end, win_end) if end else win_end
    out: list[date] = []
    d = start
    i = 0
    if frequency == "monthly":
        while d <= hi and i < MAX_OCCURRENCES:
            if d >= win_start:
                out.append(d)
            d = _add_months(d, step_interval)
            i += 1
    else:
        # weekly (default) or daily
        step = (
            timedelta(days=step_interval)
            if frequency == "daily"
            else timedelta(weeks=step_interval)
        )
        while d <= hi and i < MAX_OCCURRENCES:
            if d >= win_start:
                out.append(d)
            d = d + step
            i += 1
    return out


def _make_event(
    doc: dict[str, Any],
    external_id: str,
    start_dt: datetime,
    end_dt: datetime | None,
) -> Event:
    """Build an Event from a Domino doc + a resolved occurrence start/end."""
    title = _clean_text(doc.get("title"))
    slug = (doc.get("slug") or {}).get("current")
    url = f"{BASE_URL}/events/{slug}" if slug else None

    description = _clean_text(doc.get("description")) or None
    if description and len(description) > 2000:
        description = description[:2000].rsplit(" ", 1)[0] + "…"

    lat = doc.get("latitude")
    lng = doc.get("longitude")
    tags = _infer_tags(title, description, doc.get("tags"))

    return Event(
        id=compute_id("domino_park", external_id=external_id, url=url, title=title),
        source="domino_park",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=VENUE_NAME,
        borough=Borough.BROOKLYN,
        lat=float(lat) if isinstance(lat, (int, float)) else None,
        lng=float(lng) if isinstance(lng, (int, float)) else None,
        age_min=None,
        age_max=None,
        price=Price.UNKNOWN,  # no price field upstream
        tags=tags,
        raw_payload=json.dumps(doc, sort_keys=True, default=str),
    )


def _parse_event(
    doc: dict[str, Any],
    today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[Event]:
    """Parse one Sanity event doc into 0+ Events (expanding recurrences).

    Returns [] when filtered out, invalid, or wholly outside the window.
    """
    if not _is_kid_relevant(doc):
        return []

    title = _clean_text(doc.get("title"))
    if not title:
        logger.debug("domino_park: skipping doc with no title: id=%r", doc.get("_id"))
        return []

    start_date = _parse_date(doc.get("startDate"))
    if start_date is None:
        logger.debug("domino_park: skipping %r — no parseable startDate", title)
        return []

    doc_id = doc.get("_id")
    if not doc_id:
        logger.debug("domino_park: skipping %r — no _id", title)
        return []

    win_start, win_end = today, today + timedelta(days=window_days)
    start_hour = _parse_hour(doc.get("startHour"))
    end_hour = _parse_hour(doc.get("endHour"))
    variant = doc.get("variant")

    if variant == "reoccurring" and doc.get("frequency"):
        end_date = _parse_date(doc.get("endDate"))
        dates = _occurrence_dates(
            start_date, end_date, doc.get("frequency"), doc.get("interval"),
            win_start, win_end,
        )
        events: list[Event] = []
        for d in dates:
            events.append(
                _make_event(
                    doc,
                    external_id=f"{doc_id}:{d.isoformat()}",
                    start_dt=_combine(d, start_hour),
                    end_dt=_combine(d, end_hour) if end_hour else None,
                )
            )
        return events

    if variant == "multi-day":
        end_date = _parse_date(doc.get("endDate")) or start_date
        if end_date < start_date:
            end_date = start_date
        # Keep if the span overlaps the window at all.
        if end_date < win_start or start_date > win_end:
            return []
        return [
            _make_event(
                doc,
                external_id=str(doc_id),
                start_dt=_combine(start_date, start_hour),
                end_dt=_combine(end_date, end_hour or start_hour),
            )
        ]

    # single-day (or unset variant): exactly one event on startDate. Any
    # frequency/endDate on these docs is vestigial template data — ignore it.
    if not (win_start <= start_date <= win_end):
        return []
    return [
        _make_event(
            doc,
            external_id=str(doc_id),
            start_dt=_combine(start_date, start_hour),
            end_dt=_combine(start_date, end_hour) if end_hour else None,
        )
    ]


class DominoParkSource(Source):
    """Domino Park events via the public Sanity GROQ API."""

    name = "domino_park"

    def __init__(
        self,
        *,
        api_url: str = APICDN,
        query: str = GROQ_QUERY,
        window_days: int = DEFAULT_WINDOW_DAYS,
        http_timeout: float = 45.0,
    ):
        self._api_url = api_url
        self._query = query
        self._window_days = window_days
        self.window_days = window_days  # full-window re-fetch: missing-detection eligible
        self._timeout = http_timeout

    def fetch(self) -> Iterable[Event]:
        """Query the Sanity event collection, yielding kid-relevant Events."""
        docs = self._get_docs()
        if docs is None:
            # Hard failure already logged; ingest skips this source's rows.
            return

        today = datetime.now(NYC_TZ).date()
        total = 0
        for doc in docs:
            try:
                events = _parse_event(doc, today, self._window_days)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "domino_park: failed to parse event id=%r",
                    doc.get("_id"),
                    exc_info=True,
                )
                continue
            for ev in events:
                total += 1
                yield ev

        logger.info("domino_park: yielded %d events", total)

    def _get_docs(self) -> list[dict[str, Any]] | None:
        """Run the GROQ query. Returns the doc list, or None on HTTP/parse error."""
        url = f"{self._api_url}?query={quote(self._query)}"
        try:
            resp = cffi_requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                impersonate="chrome",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return list(data.get("result") or [])
        except Exception:  # noqa: BLE001
            logger.warning("domino_park: failed to fetch feed", exc_info=True)
            return None
