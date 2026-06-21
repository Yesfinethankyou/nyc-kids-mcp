"""Brooklyn Children's Museum events.

BCM's events page (brooklynkids.org/events) is a WordPress site with a
custom 'event' post type. Events are server-rendered HTML on paginated
listing pages — the WP REST API and iCal feed are stale and not used.

Data flow:
  1. GET /events/ (page 1), then /events/page/{N}/?tribe_paged=1 (pages 2+)
  2. Parse each <article class="tease tease-event"> block:
       date  — first div.font-black ("Saturday, June 6")
       time  — second div.font-black ("10:00 am – 5:00 pm")
       title — h2 a text
       url   — h2 a href
       desc  — trailing text div in the content column
  3. Yield Event objects; stop once the last event on a page is past the
     ingest window.

Quirks:
  - Date headers lack a year ("Saturday, June 6"). We extract the date from
    the URL slug when it contains YYYY-MM-DD (common pattern); otherwise we
    infer the year from the header using the current date as a reference.
  - Venue is always Brooklyn Children's Museum, 145 Brooklyn Avenue — hardcoded.
  - All events are PAID (museum admission required).
  - No age_min/age_max in the listing. BCM is a children's museum so all
    events are kid/family by default.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

BASE_URL = "https://www.brooklynkids.org"
EVENTS_URL_PAGE_1 = f"{BASE_URL}/events/"
EVENTS_URL_PAGE_N = f"{BASE_URL}/events/page/{{page}}/?tribe_paged=1"
VENUE_NAME = "Brooklyn Children's Museum"
VENUE_ADDRESS = "145 Brooklyn Avenue, Brooklyn, NY 11213"
REQUEST_DELAY_SECONDS = 1.0
MAX_PAGES = 15
DEFAULT_WINDOW_DAYS = 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

NYC_TZ = ZoneInfo("America/New_York")

_MONTHS: dict[str, int] = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}
_DATE_HEADER_RX = re.compile(r"\w+,\s+(\w+)\s+(\d+)")
_SLUG_DATE_RX = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_TIME_RX = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)", re.IGNORECASE)
_KID_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("family", ("family", "families", "all ages", "caregiver")),
    ("arts & crafts", ("craft", "art", "colorlab", "paint", "draw", "make")),
    ("educational", ("stem", "science", "workshop", "makerspace", "engineer", "coding")),
    ("nature", ("nature", "garden", "wildlife", "outdoor")),
    ("music", ("music", "concert", "dance", "movement", "sing")),
    ("movie", ("film", "movie", "screening", "cinema")),
    ("best for kids", ("kid", "child", "toddler", "baby", "infant", "preschool", "tot", "youth")),
]
# Skip events whose title matches — these are closure notices, not events.
_SKIP_TITLE_RX = re.compile(r"\bclosed\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pure helpers (testable without network)
# ---------------------------------------------------------------------------

def _date_from_slug(slug: str) -> date | None:
    """Extract a date from a slug like 'play-session-2-2026-06-06'."""
    m = _SLUG_DATE_RX.search(slug)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _date_from_header(text: str, today: date) -> date | None:
    """Parse 'Saturday, June 6' to a date, inferring year from today."""
    m = _DATE_HEADER_RX.match(text.strip())
    if not m:
        return None
    month = _MONTHS.get(m.group(1))
    if not month:
        return None
    day = int(m.group(2))
    # Try current year first; if the result would be more than 2 days in the
    # past (clock-drift buffer), assume next year.
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today - timedelta(days=2):
            return candidate
    return None


def _parse_time_str(text: str | None) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Parse '10:00 am – 5:00 pm' → ((10, 0), (17, 0))."""
    if not text:
        return None, None
    tokens = _TIME_RX.findall(text)
    if not tokens:
        return None, None

    def to_hm(s: str) -> tuple[int, int]:
        s = s.strip().lower()
        is_pm = "pm" in s
        s = s.replace("am", "").replace("pm", "").strip()
        h, m = int(s.split(":")[0]), int(s.split(":")[1])
        if is_pm and h != 12:
            h += 12
        elif not is_pm and h == 12:
            h = 0
        return h, m

    start_hm = to_hm(tokens[0])
    end_hm = to_hm(tokens[1]) if len(tokens) > 1 else None
    return start_hm, end_hm


def _hm_to_utc(event_date: date, hm: tuple[int, int] | None) -> datetime | None:
    if hm is None:
        return None
    h, m = hm
    local = datetime(event_date.year, event_date.month, event_date.day, h, m, tzinfo=NYC_TZ)
    return local.astimezone(UTC)


def _infer_tags(title: str, description: str | None) -> list[str]:
    haystack = title.lower() + " " + (description or "").lower()
    tags: list[str] = ["family", "best for kids"]
    for tag, keywords in _KID_KEYWORDS:
        # Leading word boundary: "art"/"make"/"draw" match as words/prefixes
        # ("artwork", "maker") but not mid-word ("smart", "filmmaker").
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", haystack) for kw in keywords
        ):
            tags.append(tag)
    return tags


def _parse_article(article_node, today: date) -> Event | None:
    """Parse one <article class='tease-event'> selectolax Node into an Event."""
    art_id = article_node.attributes.get("id", "")
    post_id = art_id.removeprefix("tease-") if art_id.startswith("tease-") else None

    # Distinguish date vs time divs by content (both have font-black class).
    date_str: str | None = None
    time_str: str | None = None
    for div in article_node.css("div.font-black"):
        text = (div.text(strip=True) or "").strip()
        if _DATE_HEADER_RX.match(text):
            date_str = text
        elif _TIME_RX.search(text):
            time_str = text

    if not date_str:
        logger.debug("bcm: article %s has no date header, skipping", post_id)
        return None

    # Title and URL
    title_a = article_node.css_first("h2 a")
    if not title_a:
        return None
    title = title_a.text(strip=True)
    if not title:
        return None
    if _SKIP_TITLE_RX.search(title):
        logger.debug("bcm: skipping closure notice %r", title)
        return None

    url = title_a.attributes.get("href") or None

    # Slug-based date takes priority (includes year); fall back to header.
    slug = (url or "").rstrip("/").rsplit("/", 1)[-1]
    event_date = _date_from_slug(slug) or _date_from_header(date_str, today)
    if event_date is None:
        logger.debug("bcm: could not parse date for %r", title)
        return None

    # Times
    start_hm, end_hm = _parse_time_str(time_str)
    start_dt = _hm_to_utc(event_date, start_hm)
    end_dt = _hm_to_utc(event_date, end_hm)

    # If no time parsed, create a naive midnight UTC start from the date
    if start_dt is None:
        start_dt = datetime(event_date.year, event_date.month, event_date.day,
                            tzinfo=NYC_TZ).astimezone(UTC)

    # Description: last plain div in the content column (the second .bcm-flex-col).
    # The slash in "w-2/3" breaks CSS selectors in selectolax, so we take the
    # last .bcm-flex-col child instead (the image column is always first).
    description: str | None = None
    flex_cols = list(article_node.css("div.bcm-flex-col"))
    content_col = flex_cols[-1] if flex_cols else None
    if content_col:
        divs = content_col.css("div")
        for d in reversed(list(divs)):
            t = (d.text(strip=True) or "").strip()
            if t and not _TIME_RX.search(t) and len(t) > 10:
                description = t
                break

    external_id = post_id or slug or None
    tags = _infer_tags(title, description)
    price = Price.FREE if description and "free" in description.lower() else Price.PAID

    raw = {"post_id": post_id, "slug": slug, "date_header": date_str, "time_str": time_str}

    return Event(
        id=compute_id("bk_childrens_museum", external_id=external_id, url=url, title=title),
        source="bk_childrens_museum",
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
        raw_payload=json.dumps(raw, sort_keys=True),
    )


def _parse_listing_page(html: str, today: date) -> list[Event]:
    """Parse all event articles from a listing page HTML string."""
    tree = HTMLParser(html)
    events: list[Event] = []
    for article in tree.css("article.tease-event"):
        try:
            ev = _parse_article(article, today)
        except Exception:
            logger.warning("bcm: failed to parse article", exc_info=True)
            continue
        if ev is not None:
            events.append(ev)
    return events


class BrooklynChildrensMuseumSource(Source):
    """Brooklyn Children's Museum events via HTML listing scrape."""

    name = "bk_childrens_museum"

    def __init__(
        self,
        *,
        events_url_page_1: str = EVENTS_URL_PAGE_1,
        events_url_page_n: str = EVENTS_URL_PAGE_N,
        window_days: int = DEFAULT_WINDOW_DAYS,
        request_delay: float = REQUEST_DELAY_SECONDS,
        http_timeout: float = 20.0,
        max_pages: int = MAX_PAGES,
    ):
        self._url_page_1 = events_url_page_1
        self._url_page_n = events_url_page_n
        self._window_days = window_days
        self.window_days = window_days  # full-window re-fetch: missing-detection eligible
        self._delay = request_delay
        self._timeout = http_timeout
        self._max_pages = max_pages

    def fetch(self) -> Iterable[Event]:
        today = datetime.now(UTC).date()
        horizon = today + timedelta(days=self._window_days)
        total = 0

        for page in range(1, self._max_pages + 1):
            url = self._url_page_1 if page == 1 else self._url_page_n.format(page=page)
            try:
                html = self._get_page(url)
            except Exception:
                logger.warning("bcm: failed to fetch page %d", page, exc_info=True)
                break

            events = _parse_listing_page(html, today)
            if not events:
                break

            past_horizon = False
            for ev in events:
                if ev.start_dt.date() > horizon:
                    past_horizon = True
                    continue
                total += 1
                yield ev

            if past_horizon:
                break

            if page < self._max_pages:
                time.sleep(self._delay)

        logger.info("bcm: yielded %d events", total)

    def _get_page(self, url: str) -> str:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=self._timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
