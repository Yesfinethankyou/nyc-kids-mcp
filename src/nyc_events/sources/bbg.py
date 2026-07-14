"""Brooklyn Botanic Garden (BBG) events.

BBG's calendar (bbg.org/visit/calendar) is a custom CMS (not WordPress —
no wp-json, no Tribe, no JSON-LD Event blocks), server-rendered and
reachable with plain httpx (no anti-bot as of 2026-07-13). BAT-style
selectolax scrape, month-page walk.

Page structure (verified live, 2026-07-13):
  - Repeated `<ul id="event-calendar-regular">` blocks, one per calendar
    day, each with its own `<h2>` date header as the ul's FIRST CHILD
    ("Sunday, July 12, 2026") followed by `<li>` event cards. The first
    block's header is "Ongoing" — undated exhibit season-runs, skipped.
  - Card: `<span class="event-tag">` (venue-curated category label, may be
    pipe-joined: "Children's Garden Classes | Children's Garden Classes"),
    `<h3>` title, `<p class="event-date">` (prose schedule for the whole
    program — the OCCURRENCE date comes from the h2 header; only the time
    range is taken from this prose), `<p class="event-blurb">` description
    (with a trailing "Learn More" span to drop), wrapping `<a href>`.
  - Month pages at /visit/calendar/month/YYYY/MM/ (the bare /visit/calendar
    is the current month). fetch() walks every month overlapping the window.

Kid-relevance strategy: CATEGORY ALLOWLIST on the event-tag label —
"Families & Kids" and "Children's Garden Classes" are venue-curated family
labels (the rest of the vocabulary is adult continuing-ed classes, member
events, evenings, tours). Same spirit as Prospect Park's category allowlist;
counts at probe time: ~12 family-tagged occurrences/month.

Quirks:
  - A recurring drop-in program ("Summer First Discoveries") appears as a
    card under EACH date it runs — the h2 date is the occurrence date, so
    `external_id = f"{url-slug}:{date}"` keys each occurrence.
  - Time prose varies: "10:30 a.m.–12:30 p.m.", "9 a.m.–1 p.m.", sometimes
    with a leading weekday/date-range segment ("Thursdays, July 16–August
    13, 2026 | 10:30 a.m.–12:30 p.m."). Only clock times with an explicit
    a.m./p.m. are matched, so the date-range numbers can't false-positive.
    No parseable time → 00:00 (all-day).
  - No price on cards → Price.UNKNOWN (most children's programming is
    "free with admission"; the Children's Garden classes are ticketed).
"""

from __future__ import annotations

import json
import logging
import re
import time as time_mod
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from ._filters import normalize
from .base import Source

NYC_TZ = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bbg.org"
CALENDAR_MONTH_URL = BASE_URL + "/visit/calendar/month/{year}/{month:02d}/"
VENUE_NAME = "Brooklyn Botanic Garden"
DEFAULT_WINDOW_DAYS = 60
PAGE_DELAY_SECONDS = 1.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# Venue-curated family labels (normalized). The tag span can pipe-join
# variants with curly/straight apostrophes, so match on normalized fragments.
_FAMILY_TAG_FRAGMENTS = ("families & kids", "children's garden")

_HEADER_DATE_RX = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),\s+(\d{4})"
)

# Clock times with an explicit meridiem only ("10:30 a.m.", "9 p.m.", "1pm") —
# bare numbers in date-range prose ("July 16–August 13") never match.
_TIME_RX = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.IGNORECASE)

_MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}

_WS_RX = re.compile(r"\s+")


def _clean(node) -> str:
    if node is None:
        return ""
    return _WS_RX.sub(" ", node.text(separator=" ", strip=True)).strip()


def _parse_header_date(text: str) -> date | None:
    """Parse 'Sunday, July 12, 2026' (the h2 section header) into a date."""
    m = _HEADER_DATE_RX.search(text)
    if not m:
        return None
    return date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))


def _to_24h(hour: int, minute: int, meridiem: str) -> tuple[int, int]:
    if meridiem == "p" and hour != 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    return (hour if hour < 24 else 0), minute


def _parse_times(prose: str) -> tuple[tuple[int, int], tuple[int, int] | None]:
    """Extract (start, end) clock times from the event-date prose.

    Returns ((0, 0), None) when no meridiem-carrying time is present
    (treated as all-day).
    """
    matches = _TIME_RX.findall(prose or "")
    if not matches:
        return (0, 0), None
    h, m, mer = matches[0]
    start = _to_24h(int(h), int(m or 0), mer.lower())
    end = None
    if len(matches) > 1:
        h, m, mer = matches[1]
        end = _to_24h(int(h), int(m or 0), mer.lower())
    return start, end


def _is_family_tagged(tag_text: str) -> bool:
    norm = normalize(tag_text).replace("’", "'")
    return any(frag in norm for frag in _FAMILY_TAG_FRAGMENTS)


def _infer_tags(tag_text: str) -> list[str]:
    tags = ["family", "best for kids", "nature"]
    if "children's garden" in normalize(tag_text).replace("’", "'"):
        tags.append("educational")
    return tags


def _parse_card(li, day: date) -> Event | None:
    """Parse one event card under a dated section header, or None."""
    title = _clean(li.css_first("h3"))
    if not title:
        return None

    tag_text = _clean(li.css_first("span.event-tag"))
    if not _is_family_tagged(tag_text):
        return None

    a = li.css_first("a[href]")
    href = (a.attributes.get("href") if a else None) or None
    url = None
    slug = None
    if href:
        url = href if href.startswith("http") else BASE_URL + href
        slug = href.rstrip("/").rsplit("/", 1)[-1] or None

    date_node = li.css_first("p.event-date")
    (hour, minute), end_hm = _parse_times(_clean(date_node))
    start_dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NYC_TZ)
    end_dt = None
    if end_hm is not None:
        end_dt = datetime(day.year, day.month, day.day, end_hm[0], end_hm[1], tzinfo=NYC_TZ)
        if end_dt <= start_dt:
            end_dt = None  # malformed/overnight prose — drop rather than invert

    blurb_node = li.css_first("p.event-blurb")
    if blurb_node is not None:
        for span in blurb_node.css("span.learnmore"):
            span.decompose()
    description = _clean(blurb_node) or None

    # One card per (program, header date) — the slug alone repeats across
    # every date the drop-in program runs, so the date joins the id.
    external_id = f"{slug}:{day.isoformat()}" if slug else None

    return Event(
        id=compute_id(
            "bbg",
            external_id=external_id,
            url=None,  # url repeats across occurrences; never a fallback key
            title=title,
            venue=VENUE_NAME,
            date_iso=day.isoformat(),
        ),
        source="bbg",
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
        price=Price.UNKNOWN,
        tags=_infer_tags(tag_text),
        # Trimmed structured extract, not the HTML blob (recipe rule for
        # scraped sources): enough to debug a field-mapping question.
        raw_payload=json.dumps(
            {
                "header_date": day.isoformat(),
                "event_tag": tag_text,
                "event_date_prose": _clean(date_node),
                "href": href,
            },
            sort_keys=True,
        ),
    )


def parse_month_page(html: str) -> list[Event]:
    """Parse one month-view calendar page into Events (pure function).

    Only cards under dated section headers pass — the "Ongoing" section
    (undated exhibit season-runs) is skipped.
    """
    tree = HTMLParser(html)
    events: list[Event] = []
    for ul in tree.css("ul#event-calendar-regular"):
        day = _parse_header_date(_clean(ul.css_first("h2")))
        if day is None:  # "Ongoing", or a nav ul reusing the id
            continue
        for li in ul.css("li"):
            try:
                ev = _parse_card(li, day)
            except Exception:  # noqa: BLE001
                logger.warning("bbg: failed to parse a card", exc_info=True)
                continue
            if ev is not None:
                events.append(ev)
    return events


def _months_in_window(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


class BBGSource(Source):
    """Brooklyn Botanic Garden family events (month-page HTML scrape)."""

    name = "bbg"
    display_name = "Brooklyn Botanic Garden"

    def __init__(
        self,
        *,
        month_url_template: str = CALENDAR_MONTH_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        page_delay: float = PAGE_DELAY_SECONDS,
        http_timeout: float = 30.0,
    ):
        self._month_url_template = month_url_template
        # Every run re-scrapes every month page overlapping the window →
        # full-window re-fetch → missing-detection eligible.
        self.window_days = window_days
        self._delay = page_delay
        self._timeout = http_timeout

    def fetch(self) -> Iterable[Event]:
        today = datetime.now(NYC_TZ).date()
        horizon = today + timedelta(days=self.window_days)
        total = 0
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            for i, (year, month) in enumerate(_months_in_window(today, horizon)):
                if i:
                    time_mod.sleep(self._delay)
                url = self._month_url_template.format(year=year, month=month)
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except Exception:  # noqa: BLE001
                    logger.warning("bbg: failed to fetch %s", url, exc_info=True)
                    continue
                for ev in parse_month_page(resp.text):
                    # A month page includes days already past (and the last
                    # page, days beyond the window) — keep the yield windowed.
                    ev_date = ev.start_dt.astimezone(NYC_TZ).date()
                    if today <= ev_date <= horizon:
                        total += 1
                        yield ev
        logger.info("bbg: yielded %d events", total)
