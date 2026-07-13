"""New York Public Library (NYPL) events — Manhattan, Bronx, Staten Island.

NYPL runs ~88 branch libraries across three boroughs (Manhattan, the Bronx,
and Staten Island; Brooklyn and Queens are separate systems — see `bpl` and
the QPL source). Its events calendar is a huge feed of youth/family
programming — storytimes, craft afternoons, teen labs, summer-learning — that
this catalog was missing. **Building this one source is what unlocks the Bronx
and Staten Island library coverage** the backlog tracked separately.

Data flow (verified live 2026-07-13):
  1. The calendar lives at `nypl.org/events/calendar` behind an Imperva
     Incapsula bot wall → fetch with `curl_cffi` Chrome impersonation.
  2. It's a **server-rendered Drupal table** with working exposed filters. We
     query per borough via the **`city[]` filter** (`bx` / `man` / `si`), so
     the borough is known from the query — no detail crawl, no branch→borough
     mapping. We add `date_op=GREATER_EQUAL&date1=today` (the listing is then
     ordered ascending by occurrence date) and the kids `audience` union as a
     hint. Pagination is `?page=N`.
  3. Each `<tr class="col-4">` row carries everything we need: the occurrence
     **date + start time** (the "Today @ 2 PM" / "Tue, July 14 @ 2 PM" cell —
     this is the authoritative occurrence, NOT the URL-path date, which is the
     event's canonical/first date and is wrong for recurring programs), title,
     description, branch (`event-location`), and an audience cell.

Quirks / load-bearing facts:
  - **The server-side `audience` filter is NOT strict** — adult-only rows come
    back too. The real kid gate is client-side: keep a row only if its
    audience cell names a youth/family term (Children / Infant / Toddlers /
    Pre-schoolers / School Age / Families / Teens). Teens-only rows are kept
    but do NOT earn the "best for kids" tag (mirrors `snug_harbor` /
    `new_york_family`).
  - **The URL is NOT unique per occurrence.** A recurring program keeps one
    URL (stamped with its canonical date) across every occurrence, and the
    audience-union can list the same occurrence twice. So
    `external_id = f"{url_path}:{start_iso}"` — this separates real occurrences
    (same URL, different times) AND collapses the duplicate listings (same URL,
    same time → same id → upsert dedups). This is the recurring-permit pattern
    from `compute_id`'s docstring.
  - **Occurrence date from the time cell, resolved into the fetch window.**
    The cell has no year; "Today"/"Tomorrow" are relative and
    "Tue, July 14" needs a year — we pick the year that lands the month/day
    inside [today, today+window_days]. Rows outside the window are dropped
    (belt-and-suspenders with the server date filter).
  - **Borough from the `city[]` query**, venue = branch name → the enrich
    pass codes neighborhood via `library_neighborhoods.json` (already ships
    every NYPL branch, borough-keyed).
  - **Price is FREE** — NYPL programs are free admission (no price/offer in
    the feed).
  - Optional **age range** parsed from the "For ages 6-12" / "For children
    ages 0-5" phrasing common in NYPL descriptions.
  - Full-window re-fetch each run (all three boroughs, every page in window)
    → opted into missing-detection (`window_days`).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from curl_cffi import requests as cffi_requests
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://www.nypl.org"
CALENDAR_URL = f"{BASE_URL}/events/calendar"
# 30-day window (maintainer call 2026-07-13): NYPL runs daily programming
# across ~88 branches, so a 60-day window produced thousands of rows that
# dominated the catalog. 30 days halves that while still covering the useful
# planning horizon. This also bounds both the fetch width and missing-detection.
DEFAULT_WINDOW_DAYS = 30
REQUEST_DELAY_SECONDS = 1.0
MAX_PAGES_PER_CITY = 200  # safety cap; ~35-45 pages/borough for a 30-day window
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# `city[]` filter value → borough. NYPL spans exactly these three.
CITY_BOROUGH: dict[str, Borough] = {
    "bx": Borough.BRONX,
    "man": Borough.MANHATTAN,
    "si": Borough.STATEN_ISLAND,
}

# Audience-term ids for the kids `audience` union (a query hint — the server
# filter is loose, so the client-side token gate below is the real filter):
# Children(+Infant/Toddler/Pre-school/School Age) + Families + Teens.
_KIDS_AUDIENCE_PARAM = "4336+4337+4338+4339+4340+4343+4356"

# Audience-cell tokens that make a row youth/family-relevant.
_KID_AUDIENCE_TOKENS = (
    "children", "infant", "toddler", "pre-school", "preschool", "pre-k",
    "school age", "families", "family", "kids", "teens", "young adult",
    "baby", "babies", "birth",
)
# Tokens that earn the "best for kids" tag (Teens/young-adult alone do not).
_BEST_FOR_KIDS_TOKENS = (
    "children", "infant", "toddler", "pre-school", "preschool", "pre-k",
    "school age", "families", "family", "kids", "baby", "babies", "birth",
)

# Keyword → tag inference from title + description.
_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("storytelling", ("storytime", "story time", "lapsit", "read-aloud", "read aloud")),
    ("arts and crafts", ("craft", "make", "art ", "drawing", "collage", "diy")),
    ("music", ("music", "sing-along", "sing along", "concert", "dance party")),
    ("theater", ("puppet", "theater", "theatre", "dance", "performance")),
    ("movies", ("film", "movie", "screening")),
    ("games", ("game", "gaming", "chess", "lego", "minecraft", "roblox")),
    ("educational", ("workshop", "class", "coding", "stem", "science",
                     "homework", "tutoring", "learning", "book club", "reading")),
    ("books", ("book", "reading", "author", "literacy")),
]

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"],
        1,
    )
}

_TIME_RX = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.IGNORECASE)
_ABS_DATE_RX = re.compile(r"([A-Za-z]+)\s+(\d{1,2})\s*$")
_AGE_RANGE_RX = re.compile(r"ages?\s+(\d{1,2})\s*[-–to]+\s*(\d{1,2})", re.IGNORECASE)
_WS_RX = re.compile(r"\s+")


@dataclass
class _RowFields:
    """Raw fields extracted from one listing row (pure, pre-filter)."""

    start_dt: datetime | None
    title: str
    url: str | None
    description: str | None
    branch: str | None
    audience: str


def _clean(text: str | None) -> str:
    return _WS_RX.sub(" ", (text or "").replace("\xa0", " ")).strip()


def _parse_occurrence_dt(
    time_cell: str, today: date, window_end: date
) -> datetime | None:
    """Parse a listing time cell ("Today @ 2 PM", "Tue, July 14 @ 2:30 PM")
    into an NY-aware datetime, resolving the (absent) year into the window."""
    text = _clean(time_cell)
    if "@" in text:
        date_part, _, time_part = text.partition("@")
    else:
        date_part, time_part = text, ""
    date_part = date_part.strip().rstrip(",").strip()

    occ: date | None = None
    low = date_part.lower()
    if low.startswith("today"):
        occ = today
    elif low.startswith("tomorrow"):
        occ = today + timedelta(days=1)
    else:
        m = _ABS_DATE_RX.search(date_part)
        if m:
            mon = _MONTHS.get(m.group(1).lower())
            day = int(m.group(2))
            if mon:
                # Pick the year that lands the date in the fetch window.
                for yr in (today.year, today.year + 1):
                    try:
                        cand = date(yr, mon, day)
                    except ValueError:
                        continue
                    if today - timedelta(days=1) <= cand <= window_end + timedelta(days=1):
                        occ = cand
                        break
    if occ is None:
        return None

    hour, minute = 0, 0
    tm = _TIME_RX.search(time_part)
    if tm:
        hour = int(tm.group(1)) % 12
        minute = int(tm.group(2) or 0)
        if tm.group(3).lower() == "p":
            hour += 12
    return datetime(occ.year, occ.month, occ.day, hour, minute, tzinfo=NYC_TZ)


def _extract_rows(html_text: str, today: date, window_end: date) -> list[_RowFields]:
    """Pure: parse every listing `<tr class="col-4">` into raw fields."""
    out: list[_RowFields] = []
    tree = HTMLParser(html_text)
    for tr in tree.css("tr.col-4"):
        link = tr.css_first("div.event-name a")
        if link is None:
            continue
        title = _clean(link.text())
        if not title:
            continue
        time_cell = tr.css_first("td.event-time")
        start_dt = (
            _parse_occurrence_dt(time_cell.text(separator=" "), today, window_end)
            if time_cell
            else None
        )
        desc_el = tr.css_first("div.description")
        loc_el = tr.css_first("td.event-location")
        aud_el = tr.css_first("td.event-audience")
        out.append(
            _RowFields(
                start_dt=start_dt,
                title=title,
                url=link.attributes.get("href"),
                description=_clean(desc_el.text(separator=" ")) if desc_el else None,
                branch=_clean(loc_el.text(separator=" ")) if loc_el else None,
                audience=_clean(aud_el.text(separator=" ")).lower() if aud_el else "",
            )
        )
    return out


def _is_kid_audience(audience: str) -> bool:
    return any(tok in audience for tok in _KID_AUDIENCE_TOKENS)


def _infer_tags(title: str, description: str | None, audience: str) -> list[str]:
    tags: list[str] = ["family"]
    if any(tok in audience for tok in _BEST_FOR_KIDS_TOKENS):
        tags.append("best for kids")
    haystack = f"{title} {description or ''}".lower()
    for tag, keywords in _TAG_RULES:
        if tag not in tags and any(kw in haystack for kw in keywords):
            tags.append(tag)
    return tags


def _parse_age_range(description: str | None) -> tuple[int | None, int | None]:
    if not description:
        return None, None
    m = _AGE_RANGE_RX.search(description)
    if not m:
        return None, None
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo > hi or hi > 18:
        return None, None
    return lo, hi


def _abs_url(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith("http") else BASE_URL + url


def parse_rows(
    html_text: str,
    borough: Borough,
    today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[Event]:
    """Pure: parse one listing page into kid-relevant, in-window Events for the
    given borough. Exercised directly by the fixture test (no network)."""
    window_end = today + timedelta(days=window_days)
    events: list[Event] = []
    for row in _extract_rows(html_text, today, window_end):
        if row.start_dt is None:
            continue
        if not (today <= row.start_dt.date() <= window_end):
            continue
        if not _is_kid_audience(row.audience):
            continue
        # Virtual programs ("Online" branch) have no physical location — drop
        # them: they carry no real borough and would otherwise be ingested
        # three times (once per borough query) with three wrong boroughs.
        if not row.branch or row.branch.lower().startswith("online"):
            continue
        url = _abs_url(row.url)
        # url is not unique per occurrence → key on url-path + start minute.
        occ_key = f"{row.url}:{row.start_dt.strftime('%Y-%m-%dT%H:%M')}"
        age_min, age_max = _parse_age_range(row.description)
        events.append(
            Event(
                id=compute_id("nypl", external_id=occ_key, url=url, title=row.title),
                source="nypl",
                external_id=occ_key,
                title=row.title,
                description=row.description or None,
                url=url,
                start_dt=row.start_dt,
                end_dt=None,
                venue_name=row.branch or None,
                borough=borough,
                lat=None,
                lng=None,
                age_min=age_min,
                age_max=age_max,
                price=Price.FREE,
                tags=_infer_tags(row.title, row.description, row.audience),
                raw_payload=None,
            )
        )
    return events


class NYPLSource(Source):
    """NYPL branch events via the server-rendered Drupal calendar (per borough)."""

    name = "nypl"

    def __init__(
        self,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
        request_delay: float = REQUEST_DELAY_SECONDS,
        http_timeout: float = 30.0,
        max_pages_per_city: int = MAX_PAGES_PER_CITY,
    ):
        # Full-window re-fetch of all three boroughs → missing-detection eligible.
        self.window_days = window_days
        self._delay = request_delay
        self._timeout = http_timeout
        self._max_pages = max_pages_per_city

    def fetch(self) -> Iterable[Event]:
        today = datetime.now(NYC_TZ).date()
        window_end = today + timedelta(days=self.window_days)
        for city, borough in CITY_BOROUGH.items():
            yield from self._fetch_city(city, borough, today, window_end)

    def _fetch_city(
        self, city: str, borough: Borough, today: date, window_end: date
    ) -> Iterable[Event]:
        base = (
            f"{CALENDAR_URL}?audience={_KIDS_AUDIENCE_PARAM}"
            f"&date_op=GREATER_EQUAL&date1={today.strftime('%m/%d/%Y')}"
            f"&city%5B%5D={city}"
        )
        yielded = 0
        seen_ids: set[str] = set()
        for page in range(self._max_pages):
            html_text = self._get_page(f"{base}&page={page}")
            if html_text is None:
                break
            rows = _extract_rows(html_text, today, window_end)
            if not rows:
                break
            for ev in parse_rows(html_text, borough, today, self.window_days):
                # The audience-union lists some occurrences twice (same url +
                # same time → same id); the upsert would dedup on write, but
                # skip here so the yield count and missing-detection ratios are
                # honest.
                if ev.id in seen_ids:
                    continue
                seen_ids.add(ev.id)
                yielded += 1
                yield ev
            # Ascending by occurrence date: once every dated row on the page is
            # past the window, later pages are too.
            dated = [r.start_dt.date() for r in rows if r.start_dt is not None]
            if dated and min(dated) > window_end:
                break
            time.sleep(self._delay)
        logger.info("nypl: %s (%s) yielded %d events", city, borough.value, yielded)

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
            logger.warning("nypl: failed to fetch %s", url, exc_info=True)
            return None
