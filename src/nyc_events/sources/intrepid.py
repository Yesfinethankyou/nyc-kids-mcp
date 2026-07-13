"""Intrepid Sea, Air & Space Museum events.

The Intrepid Museum (the aircraft-carrier / space-shuttle museum at Pier 86,
Hell's Kitchen, Manhattan) is a family destination: school-break camps,
"Early Morning Opening" family programs, Astronomy Nights, Movie Nights,
Access Family Programs, and STEM workshops — plus a strand of adult
programming (the "Intrepid After Hours" evening series, tasting fests, galas,
private rentals) that we filter out.

Data flow (verified live 2026-07-13):
  1. The calendar is at `intrepidmuseum.org/events/calendar` (Drupal) behind a
     bot wall → fetch with `curl_cffi` Chrome impersonation. It's a
     server-rendered card grid; **GET `?page=N` paginates cleanly** (6 cards
     per page, ascending by date) — the Drupal `/views/ajax` POST pager does
     NOT work (returns page 0 every time), so we use the GET pager.
  2. Each `div.card.product-card` carries a title (`.card--header.h6`), a start
     and end `<time datetime>` (ISO with a real offset — authoritative), a
     description (`.card--body`), and the detail `<a href>`.

Quirks / decisions:
  - **Inclusive + adult-blocklist** kid gate (not an allowlist): a museum
    calendar is family-oriented by default, so we keep everything except the
    adult strand — the shared `ADULT_BLOCKLIST` / `ADULT_TITLE_BLOCKLIST` /
    `MEMBERS_ONLY` plus local signals (`after hours`, `tasting`, `gala`,
    `cocktail`). This mirrors Brooklyn Army Terminal's strategy.
  - **`external_id = url:start_iso`** — a recurring program (e.g. "Free World
    Cup Watch Parties") reuses one detail URL across its dated occurrences,
    so the start datetime keys each occurrence.
  - Single fixed venue → `SOURCE_NEIGHBORHOOD["intrepid"] = "Hell's Kitchen"`
    (the feed has no lat/lng). Borough Manhattan, venue "Intrepid Museum".
  - **Price:** UNKNOWN by default; FREE when the title says so ("Free World
    Cup Watch Parties").
  - Full-window re-fetch each run → opted into missing-detection.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from curl_cffi import requests as cffi_requests
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from ._filters import (
    ADULT_BLOCKLIST,
    ADULT_TITLE_BLOCKLIST,
    MEMBERS_ONLY,
    contains_any,
)
from .base import Source

logger = logging.getLogger(__name__)

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://intrepidmuseum.org"
CALENDAR_URL = f"{BASE_URL}/events/calendar"
VENUE_NAME = "Intrepid Museum"
DEFAULT_WINDOW_DAYS = 60
REQUEST_DELAY_SECONDS = 1.0
MAX_PAGES = 40  # safety cap; ~6 cards/page, calendar runs a few months out
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# Local adult-strand signals on top of the shared filter sets.
_LOCAL_EXCLUDE: tuple[str, ...] = ("after hours", "tasting", "gala", "cocktail")

# Title/description keyword → tag.
_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("educational", ("science", "stem", "workshop", "astronomy", "astro",
                     "academy", "learn", "history", "engineering", "coding")),
    ("outer space", ("space", "astro", "planet", "rocket", "shuttle", "cosmos")),
    ("movies", ("movie", "film", "screening")),
    ("aviation", ("flight", "aviation", "aircraft", "plane", "concorde", "soaring")),
    ("best for kids", ("family", "kids", "children", "camp", "sail-a-bration")),
]


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return dt.replace(tzinfo=NYC_TZ) if dt.tzinfo is None else dt.astimezone(NYC_TZ)


def _is_kid_relevant(title: str, description: str) -> bool:
    haystack = f"{title} {description}".lower()
    if contains_any(haystack, ADULT_BLOCKLIST) or contains_any(haystack, _LOCAL_EXCLUDE):
        return False
    if contains_any(title.lower(), ADULT_TITLE_BLOCKLIST) or contains_any(
        title.lower(), MEMBERS_ONLY
    ):
        return False
    return True


def _infer_tags(title: str, description: str) -> list[str]:
    haystack = f"{title} {description}".lower()
    tags: list[str] = ["family"]
    for tag, keywords in _TAG_RULES:
        if tag not in tags and any(kw in haystack for kw in keywords):
            tags.append(tag)
    return tags


def _price(title: str) -> Price:
    return Price.FREE if "free" in title.lower() else Price.UNKNOWN


def parse_calendar(
    html_text: str, today: date, window_days: int = DEFAULT_WINDOW_DAYS
) -> list[Event]:
    """Pure: parse one calendar page into kid-relevant, in-window Events.
    Exercised directly by the fixture test (no network)."""
    window_end = today + timedelta(days=window_days)
    events: list[Event] = []
    tree = HTMLParser(html_text)
    for card in tree.css("div.card.product-card"):
        title_el = card.css_first(".card--header.h6")
        title = title_el.text(strip=True) if title_el else ""
        if not title:
            continue
        times = card.css("time")
        start_dt = _parse_dt(times[0].attributes.get("datetime")) if times else None
        if start_dt is None:
            continue
        if not (today <= start_dt.date() <= window_end):
            continue
        end_dt = _parse_dt(times[1].attributes.get("datetime")) if len(times) > 1 else None
        body_el = card.css_first(".card--body")
        description = body_el.text(separator=" ", strip=True) if body_el else ""
        if not _is_kid_relevant(title, description):
            continue
        link = card.css_first("a")
        href = link.attributes.get("href") if link else None
        url = (BASE_URL + href) if href and href.startswith("/") else (href or None)
        occ_key = f"{href}:{start_dt.strftime('%Y-%m-%dT%H:%M')}" if href else None
        events.append(
            Event(
                id=compute_id("intrepid", external_id=occ_key, url=url, title=title),
                source="intrepid",
                external_id=occ_key,
                title=title,
                description=description or None,
                url=url,
                start_dt=start_dt,
                end_dt=end_dt,
                venue_name=VENUE_NAME,
                borough=Borough.MANHATTAN,
                lat=None,
                lng=None,
                age_min=None,
                age_max=None,
                price=_price(title),
                tags=_infer_tags(title, description),
                raw_payload=None,
            )
        )
    return events


class IntrepidSource(Source):
    """Intrepid Museum events via the server-rendered Drupal calendar."""

    name = "intrepid"

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
        for page in range(self._max_pages):
            html_text = self._get_page(page)
            if html_text is None:
                break
            tree = HTMLParser(html_text)
            cards = tree.css("div.card.product-card")
            if not cards:
                break
            for ev in parse_calendar(html_text, today, self.window_days):
                if ev.id in seen_ids:
                    continue
                seen_ids.add(ev.id)
                total += 1
                yield ev
            # Ascending by date: once every dated card is past the window, stop.
            dated = [
                _parse_dt(c.css_first("time").attributes.get("datetime")).date()
                for c in cards
                if c.css_first("time")
                and _parse_dt(c.css_first("time").attributes.get("datetime"))
            ]
            if dated and min(dated) > window_end:
                break
            time.sleep(self._delay)
        logger.info("intrepid: yielded %d events", total)

    def _get_page(self, page: int) -> str | None:
        try:
            resp = cffi_requests.get(
                f"{CALENDAR_URL}?page={page}",
                headers={"User-Agent": USER_AGENT},
                impersonate="chrome",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.text
        except Exception:  # noqa: BLE001
            logger.warning("intrepid: failed to fetch page %d", page, exc_info=True)
            return None
