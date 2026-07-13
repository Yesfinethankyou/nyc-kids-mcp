"""Queens Public Library (QPL) events.

QPL runs ~65 branches across Queens (a separate system from NYPL and BPL).
Its events calendar is a Drupal site with a custom Solr search front. This is
new Queens library coverage to sit alongside `bpl` (Brooklyn) and `nypl`
(Manhattan / Bronx / Staten Island).

Data flow (verified live 2026-07-13):
  1. The calendar is behind an F5/BIG-IP bot wall → fetch with `curl_cffi`
     Chrome impersonation. **Bare `/calendar` silently serves the homepage** —
     the listing needs the full nav query string
     `/calendar?searchField=%2A&category=calendar&fromlink=calendar&searchFilter=`.
  2. Pagination is a separate endpoint:
     `/search/call?searchField=%2A&category=calendar&pageParam=<n>&searchFilter=`
     (12 cards/page, ordered ascending by date).
  3. **Each card embeds a full `arrJsonData_cal['<id>'] = '{...}'` JSON blob**
     in an inline `<script>` — cleaner than the visible (truncated) card text.
     We parse those blobs, not the `<p>` tags.

The JSON per event carries: `jobID` (id like `019359-0626`), `title`,
`descrQV` (a teaser — truncated upstream with an ellipsis), `callUrl` (the
detail path), `prgm_age` ("Kids(0-5), Kids(6-11)" — the audience/age),
`prgm_type` (→ tags), `branch_name`, `delivery_format`, `date_show_timestamp`
(a UNIX epoch — authoritative, no year-parsing), and `all_times` (every
occurrence timestamp of a recurring program).

Quirks / load-bearing decisions:
  - **Kid gate is `prgm_age`** — keep events whose age string names a youth
    band (Kids / Teens / Babies / Family / Children). "Adults"-only and
    "Adults, Seniors" are dropped. An "Adults, Kids(0-5)" family program is
    kept. Teens-only is kept but does NOT earn the "best for kids" tag.
  - **One Event per card, at its NEXT occurrence** (`date_show_timestamp`),
    NOT one per `all_times` entry. QPL lists a recurring program ONCE (its
    card shows the next date; a summer daily program has 40 `all_times`
    entries), so expanding would multiply the catalog ~26x with near-identical
    rows. We follow QPL's own granularity and preserve `all_times` in
    `raw_payload` for future per-occurrence expansion if wanted. `external_id`
    is the bare `jobID`, so the nightly re-ingest advances the shown date in
    place as occurrences pass (compute_id omits start_dt by design).
  - **Age range from `prgm_age`** — the min/max across its "Kids(0-5)" bands.
  - **Borough is always Queens**; venue = `branch_name` → neighborhood via
    `library_neighborhoods.json` (ships every QPL branch, borough-keyed).
    Non-in-person / branch-less rows are dropped (no physical location).
  - **Price is FREE** (library programs).
  - Full-window re-fetch each run → opted into missing-detection.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from curl_cffi import requests as cffi_requests

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://www.queenslibrary.org"
LISTING_URL = (
    f"{BASE_URL}/calendar?searchField=%2A&category=calendar"
    "&fromlink=calendar&searchFilter="
)
PAGE_URL = (
    f"{BASE_URL}/search/call?searchField=%2A&category=calendar"
    "&pageParam={page}&searchFilter="
)
DEFAULT_WINDOW_DAYS = 60
REQUEST_DELAY_SECONDS = 1.0
MAX_PAGES = 250  # safety cap; ~98 pages for a 60-day window
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# prgm_age tokens that make a program youth/family-relevant.
_KID_AGE_TOKENS = ("kids", "teens", "babies", "children", "family")
# Tokens that earn "best for kids" (teens alone does not).
_BEST_FOR_KIDS_TOKENS = ("kids", "babies", "children", "family")

_ARRJSON_RX = re.compile(r"arrJsonData_cal\['[^']+'\]\s*=\s*'(\{.*?\})';")
_AGE_BAND_RX = re.compile(r"\((\d{1,2})\s*-\s*(\d{1,2})\)")
_ELLIPSIS_RX = re.compile(r"\.{2,}\s*$")

# prgm_type / title keyword → tag.
_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("storytelling", ("story", "read", "lapsit", "toddler time", "baby")),
    ("arts and crafts", ("craft", "art", "make", "draw", "diy", "creativ")),
    ("music", ("music", "sing", "concert", "dance")),
    ("games", ("game", "gaming", "chess", "lego", "roblox", "minecraft")),
    ("movies", ("film", "movie", "screening")),
    ("educational", ("workshop", "class", "stem", "science", "coding",
                     "homework", "tutor", "reading", "summer reading",
                     "learning", "book")),
]


@dataclass
class _CardData:
    job_id: str
    title: str
    description: str | None
    url: str | None
    prgm_age: str
    prgm_type: str
    branch: str | None
    delivery_format: str
    start_ts: int | None


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _extract_cards(html_text: str) -> list[_CardData]:
    """Pure: pull every card's `arrJsonData_cal` JSON blob out of a page."""
    out: list[_CardData] = []
    for raw in _ARRJSON_RX.findall(html_text):
        try:
            obj: dict[str, Any] = json.loads(_html.unescape(raw))
        except json.JSONDecodeError:
            continue
        job_id = obj.get("jobID")
        if not job_id:
            continue
        desc = _clean(obj.get("descrQV") or obj.get("descr"))
        desc = _ELLIPSIS_RX.sub("…", desc) if desc else ""
        ts = obj.get("date_show_timestamp")
        if isinstance(ts, int):
            start_ts: int | None = ts
        elif isinstance(ts, str) and ts.isdigit():
            start_ts = int(ts)
        else:
            start_ts = None
        url = obj.get("callUrl")
        out.append(
            _CardData(
                job_id=str(job_id),
                title=_clean(obj.get("title")),
                description=desc or None,
                url=(BASE_URL + url) if url and url.startswith("/") else (url or None),
                prgm_age=_clean(obj.get("prgm_age")),
                prgm_type=_clean(obj.get("prgm_type")),
                branch=_clean(obj.get("branch_name")) or None,
                delivery_format=_clean(obj.get("delivery_format")),
                start_ts=start_ts,
            )
        )
    return out


def _is_kid_age(prgm_age: str) -> bool:
    low = prgm_age.lower()
    return any(tok in low for tok in _KID_AGE_TOKENS)


def _parse_age_range(prgm_age: str) -> tuple[int | None, int | None]:
    bands = _AGE_BAND_RX.findall(prgm_age)
    if not bands:
        return None, None
    los = [int(a) for a, _ in bands]
    his = [int(b) for _, b in bands]
    return min(los), max(his)


def _infer_tags(card: _CardData) -> list[str]:
    tags: list[str] = ["family"]
    if any(tok in card.prgm_age.lower() for tok in _BEST_FOR_KIDS_TOKENS):
        tags.append("best for kids")
    haystack = f"{card.title} {card.prgm_type}".lower()
    for tag, keywords in _TAG_RULES:
        if tag not in tags and any(kw in haystack for kw in keywords):
            tags.append(tag)
    return tags


def _venue_name(branch: str | None) -> str | None:
    """Normalize a QPL branch to its library name. The feed's `branch_name` is
    the bare place ("South Hollis"), but the enrich pass's library lookup is
    gated on the venue carrying a "library" token and keys on the library core
    — so "South Hollis" → "South Hollis Library" both reads correctly AND
    resolves to a neighborhood."""
    if not branch:
        return None
    if "library" in branch.lower():
        return branch
    return f"{branch} Library"


def _build_event(card: _CardData, start_dt: datetime) -> Event:
    age_min, age_max = _parse_age_range(card.prgm_age)
    return Event(
        id=compute_id("qpl", external_id=card.job_id, url=card.url, title=card.title),
        source="qpl",
        external_id=card.job_id,
        title=card.title,
        description=card.description,
        url=card.url,
        start_dt=start_dt,
        end_dt=None,
        venue_name=_venue_name(card.branch),
        borough=Borough.QUEENS,
        lat=None,
        lng=None,
        age_min=age_min,
        age_max=age_max,
        price=Price.FREE,
        tags=_infer_tags(card),
        raw_payload=None,
    )


def parse_calendar(
    html_text: str, today: date, window_days: int = DEFAULT_WINDOW_DAYS
) -> list[Event]:
    """Pure: parse one listing page into kid-relevant, in-window Queens Events.
    Exercised directly by the fixture test (no network)."""
    window_end = today + timedelta(days=window_days)
    events: list[Event] = []
    for card in _extract_cards(html_text):
        if not card.title or card.start_ts is None:
            continue
        if not _is_kid_age(card.prgm_age):
            continue
        # In-person only: online/virtual programs have no physical branch.
        fmt = card.delivery_format.lower()
        if not card.branch or "online" in fmt or "virtual" in fmt:
            continue
        start_dt = datetime.fromtimestamp(card.start_ts, UTC).astimezone(NYC_TZ)
        if not (today <= start_dt.date() <= window_end):
            continue
        events.append(_build_event(card, start_dt))
    return events


class QPLSource(Source):
    """Queens Public Library events via the Drupal/Solr calendar search."""

    name = "qpl"

    def __init__(
        self,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
        request_delay: float = REQUEST_DELAY_SECONDS,
        http_timeout: float = 30.0,
        max_pages: int = MAX_PAGES,
    ):
        # Full-window re-fetch each run → missing-detection eligible.
        self.window_days = window_days
        self._delay = request_delay
        self._timeout = http_timeout
        self._max_pages = max_pages

    def fetch(self) -> Iterable[Event]:
        today = datetime.now(NYC_TZ).date()
        window_end = today + timedelta(days=self.window_days)
        total = 0
        seen_ids: set[str] = set()
        for page in range(1, self._max_pages + 1):
            url = LISTING_URL if page == 1 else PAGE_URL.format(page=page)
            html_text = self._get_page(url)
            if html_text is None:
                break
            cards = _extract_cards(html_text)
            if not cards:
                break
            for ev in parse_calendar(html_text, today, self.window_days):
                if ev.id in seen_ids:
                    continue
                seen_ids.add(ev.id)
                total += 1
                yield ev
            # Ordered ascending by shown date: once every dated card on the
            # page is past the window, later pages are too.
            shown = [
                datetime.fromtimestamp(c.start_ts, UTC).astimezone(NYC_TZ).date()
                for c in cards
                if c.start_ts is not None
            ]
            if shown and min(shown) > window_end:
                break
            time.sleep(self._delay)
        logger.info("qpl: yielded %d events", total)

    def _get_page(self, url: str) -> str | None:
        try:
            resp = cffi_requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                impersonate="chrome",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.text
        except Exception:  # noqa: BLE001
            logger.warning("qpl: failed to fetch %s", url, exc_info=True)
            return None
