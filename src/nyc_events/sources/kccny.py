"""Korean Cultural Center New York (KCCNY) — koreanculture.org.

KCCNY (122 E. 32nd Street, Manhattan — Murray Hill/Kips Bay) is the NYC
branch of Korea's Ministry of Culture, Sports and Tourism. Programming is
mostly adult (concerts, dance, author talks, language classes) but the
`/education-literature` collection carries a real, recurring kids/family
strand: a roughly-monthly "Korean Storytime" series, family days
(Seollal/Lunar New Year), and kid workshops ("A K-Birthday Party" — a
drop-off class). Verified live 2026-07-23; see SOURCES-BACKLOG.md for the
full probe writeup this module was built against.

Scope: `/education-literature` ONLY for v1. The other collections
(`/performing-arts`, `/films`, etc.) were probed at ~1% kid-relevant
(`/performing-arts`) and not individually scanned — not worth the fetch
cost. Revisit if `/education-literature` alone proves too thin.

Platform: Squarespace, but NOT the `?format=json` fast path — this site's
`robots.txt` explicitly disallows `?format=json` (and `&format=json`,
`format=ical`, etc.) for all user agents, so the JSON shortcut documented
for Coney Island USA is off the table here. `?offset=` (the plain
pagination param) is NOT in the disallow list, so this is a plain HTML
scrape: `GET /education-literature` (and `?offset=<ms>` for older pages)
server-renders `<article class="BlogList-item ...">` cards with the same
data. Verified live: plain `httpx` works, no anti-bot / no `curl_cffi`
needed (unusual for a consumer-facing site — don't "upgrade" to curl_cffi
without re-probing first).

No structured date field exists anywhere on this platform (detail-page
JSON-LD is Article/Organization/WebSite/LocalBusiness, never Event) — the
date/venue/age all live in free-text prose inside `.BlogList-item-excerpt`,
in several formats:
  - Single date+time: "Wednesday, October 22, 2025, 4:00–5:00 PM"
  - Two-line multi-session (each line its own occurrence, same post):
    "Friday, August 7, 2026, 4:00–5:30 PM" / "Saturday, August 8, 2026,
    3:00–4:30 PM" — expanded into two Events, `new_york_family`/`bbg`-style.
  - "Date: October 30th, 2023 @ 4pm" prefix style (older posts).
  - Age info, when present, is also prose ("Recommended Ages: 6–9",
    "Designed for ages 4–6") — regex-extracted, not upstream-structured.
Time ranges are typically written with a SINGLE trailing meridiem covering
both ends ("4:00–5:30 PM", not "4:00 PM–5:30 PM") — `_RANGE_RX` allows an
absent meridiem on the first number and borrows the second's.

Some rows have an EMPTY excerpt (e.g. "Seollal Family Day" in the fixture)
— the date exists only in the full detail-page body in that case. Per
explicit scope decision for v1, this source does NOT crawl detail pages
(unlike `mommy_poppins`/`snug_harbor`) — a card with no parseable date in
its excerpt is skipped (logged), not fetched. This trades a small amount
of recall (empty-excerpt rows) for staying a cheap list-only scrape.

Kid-relevance filtering: this is a mixed adult+kids site (not a curated
kids feed like `bk_childrens_museum`), so filtering is an INCLUSIVE
keyword allowlist on title/excerpt (`storytime`, `kids`, `family`,
`families`, `children`, `toddler`, `birthday party`, or an explicit age-
range phrase) plus the shared `ADULT_BLOCKLIST`/`ADULT_TITLE_BLOCKLIST`/
`MEMBERS_ONLY` safety net from `_filters.py` (a cultural center runs
galas/ticketed talks too).

`category-past`/`category-Past` classes are a staff-maintained "already
happened" hint but NOT used as the filter — list pages are ordered by
publish date, not event date, so a past-tagged card can sit above an
upcoming one. Instead every card's real occurrence date(s) are parsed and
filtered against `[today, today + window_days]` in `fetch()`, the same
window-filter shape as `bbg`/`nypl`.

`external_id`: the Squarespace item id (`data-item-id`, a stable hex
string) is per-POST, not per-occurrence — a multi-session post ("A
K-Birthday Party") would collapse its two sessions into one row without
help. Always bind `external_id = f"{item_id}:{occurrence_date.isoformat()}"`
(even for single-occurrence posts, for consistency — same pattern as
`bbg`/`nyc_permitted_events`/`new_york_family`).

Venue is fixed: "Korean Cultural Center New York", 122 E. 32nd Street,
Manhattan. Do NOT use the JSON-LD `Organization`/`LocalBusiness` address
(460 Park Avenue) — that's the org's separate mailing address; every
sampled event excerpt names 122 E. 32nd Street explicitly, and the rare
off-site row (e.g. "The Other Korea" at The Town Hall) is an adult
performing-arts post filtered out by the keyword allowlist anyway. Single
fixed venue -> `SOURCE_NEIGHBORHOOD["kccny"] = "Murray Hill"` (a substring
of the official NTA "Murray Hill-Kips Bay", matching the tier-1 label
convention).

Missing-detection: opted IN. `/education-literature` is small (~235 items,
~12 pages of 20) so a full page-walk every run is cheap, and it's a real
full-window re-fetch (not incremental sitemap-lastmod discovery), so an
in-window future event that disappears is a real signal.

Price: no structured cost field. "free"-mentioning prose -> Price.FREE;
a "$" amount or "tickets" mention -> Price.PAID; otherwise UNKNOWN.
"""

from __future__ import annotations

import json
import logging
import re
import time as time_mod
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, ADULT_TITLE_BLOCKLIST, MEMBERS_ONLY, contains_any
from .base import Source

logger = logging.getLogger(__name__)

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://www.koreanculture.org"
COLLECTION_URL = BASE_URL + "/education-literature"
VENUE_NAME = "Korean Cultural Center New York"
DEFAULT_WINDOW_DAYS = 60
PAGE_DELAY_SECONDS = 1.0
MAX_PAGES = 30  # safety cap; ~235 items / 20-per-page ~= 12 pages observed
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# Inclusive keyword allowlist (normalized — see _filters.normalize). This site
# is mixed adult+kids, not a curated kids feed, so a keyword (or an explicit
# age-range phrase, checked separately via _AGE_RX) is REQUIRED to keep a row.
_KID_KEYWORDS: tuple[str, ...] = (
    "storytime", "story time", "kids", "family", "families", "children",
    "toddler", "birthday party", "all ages",
)

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

_DATE_RX = re.compile(
    r"(" + "|".join(m.capitalize() for m in _MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
    re.IGNORECASE,
)

# Time range where the first number's meridiem is often omitted and implied by
# the second ("4:00–5:30 PM"): first meridiem group is optional, second is
# required. Falls back to _SINGLE_TIME_RX when there's no dash at all.
_RANGE_RX = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?m\.?)?\s*[–—-]\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?",
    re.IGNORECASE,
)
_SINGLE_TIME_RX = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.IGNORECASE)

_AGE_RX = re.compile(r"ages?:?\s*(\d{1,2})\s*[–—-]\s*(\d{1,2})", re.IGNORECASE)

_WS_RX = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    return _WS_RX.sub(" ", (text or "")).strip()


def _to_24h(hour: int, minute: int, meridiem: str) -> tuple[int, int]:
    if meridiem == "p" and hour != 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    return (hour if hour < 24 else 0), minute


def _parse_time_range(text: str) -> tuple[tuple[int, int], tuple[int, int] | None]:
    """Extract (start, end) clock times from one excerpt line.

    Handles "4:00–5:30 PM" (single trailing meridiem covering both ends,
    the common KCCNY style) as well as a lone "3:00 PM" / "4pm". No
    parseable time -> ((0, 0), None) (all-day).
    """
    m = _RANGE_RX.search(text)
    if m:
        h1, m1, mer1, h2, m2, mer2 = m.groups()
        end = _to_24h(int(h2), int(m2 or 0), mer2.lower())
        start = _to_24h(int(h1), int(m1 or 0), (mer1 or mer2).lower())
        return start, end
    m = _SINGLE_TIME_RX.search(text)
    if m:
        h, mi, mer = m.groups()
        return _to_24h(int(h), int(mi or 0), mer.lower()), None
    return (0, 0), None


def _parse_occurrence_lines(
    excerpt_text: str,
) -> list[tuple[date, tuple[int, int], tuple[int, int] | None]]:
    """Find every date-bearing line in the excerpt prose -> (date, start, end).

    A multi-session post has one line per session (see module docstring);
    lines without a recognizable "Month Day, Year" are skipped (venue/
    address/age-info lines).
    """
    occurrences = []
    for line in excerpt_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _DATE_RX.search(line)
        if not m:
            continue
        month = _MONTHS[m.group(1).lower()]
        try:
            occ_date = date(int(m.group(3)), month, int(m.group(2)))
        except ValueError:
            continue
        start, end = _parse_time_range(line)
        occurrences.append((occ_date, start, end))
    return occurrences


def _parse_age_range(text: str) -> tuple[int | None, int | None]:
    m = _AGE_RX.search(text)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _parse_price(text: str) -> Price:
    norm = text.lower()
    if "free" in norm:
        return Price.FREE
    if "$" in norm or "ticket" in norm:
        return Price.PAID
    return Price.UNKNOWN


def _infer_tags(title: str, excerpt_text: str) -> list[str]:
    haystack = f"{title} {excerpt_text}".lower()
    tags = ["family", "best for kids"]
    if "storytime" in haystack or "story time" in haystack:
        tags.append("story time")
    if "birthday" in haystack:
        tags.append("educational")
    if "craft" in haystack:
        tags.append("arts and crafts")
    return tags


def _is_kid_relevant(title: str, excerpt_text: str) -> bool:
    combined = f"{title} {excerpt_text}"
    if contains_any(combined, ADULT_BLOCKLIST):
        return False
    if contains_any(title, ADULT_TITLE_BLOCKLIST) or contains_any(title, MEMBERS_ONLY):
        return False
    if contains_any(combined, _KID_KEYWORDS):
        return True
    return bool(_AGE_RX.search(excerpt_text))


def _parse_card(article) -> list[Event]:
    """Parse one `<article class="BlogList-item">` into 0+ Events (pure).

    Returns one Event per parsed occurrence date (a multi-session post
    yields multiple Events). Returns [] when filtered out, or when no
    excerpt/date is present (this source does not crawl detail pages —
    see module docstring).
    """
    title_node = article.css_first(".BlogList-item-title")
    if title_node is None:
        return []
    title = _clean(title_node.text())
    if not title:
        return []

    href = title_node.attributes.get("href")
    url = urljoin(BASE_URL, href) if href else None

    excerpt_node = article.css_first(".BlogList-item-excerpt")
    raw_lines = excerpt_node.text(separator="\n", strip=True).split("\n") if excerpt_node else []
    # Drop blank lines and the trailing "Read More" link text.
    excerpt_lines = "\n".join(
        line.strip() for line in raw_lines if line.strip() and line.strip().lower() != "read more"
    )
    excerpt_text = _clean(excerpt_lines)

    if not _is_kid_relevant(title, excerpt_text):
        return []

    occurrences = _parse_occurrence_lines(excerpt_lines)
    if not occurrences:
        logger.debug("kccny: skipping %r — no parseable occurrence date", title)
        return []

    item_id = article.attributes.get("data-item-id") or (
        article.attributes.get("id") or ""
    ).removeprefix("post-")
    price = _parse_price(excerpt_text)
    age_min, age_max = _parse_age_range(excerpt_text)
    tags = _infer_tags(title, excerpt_text)
    class_attr = article.attributes.get("class") or ""

    events: list[Event] = []
    for occ_date, (sh, sm), end_hm in occurrences:
        start_dt = datetime(occ_date.year, occ_date.month, occ_date.day, sh, sm, tzinfo=NYC_TZ)
        end_dt = None
        if end_hm is not None:
            end_dt = datetime(
                occ_date.year, occ_date.month, occ_date.day, end_hm[0], end_hm[1], tzinfo=NYC_TZ
            )
            if end_dt <= start_dt:
                end_dt = None  # malformed prose — drop rather than invert

        external_id = f"{item_id}:{occ_date.isoformat()}" if item_id else None

        events.append(
            Event(
                id=compute_id(
                    "kccny",
                    external_id=external_id,
                    url=None,  # url repeats across occurrences — never a fallback key
                    title=title,
                    venue=VENUE_NAME,
                    date_iso=occ_date.isoformat(),
                ),
                source="kccny",
                external_id=external_id,
                title=title,
                description=excerpt_text or None,
                url=url,
                start_dt=start_dt,
                end_dt=end_dt,
                venue_name=VENUE_NAME,
                borough=Borough.MANHATTAN,
                lat=None,
                lng=None,
                age_min=age_min,
                age_max=age_max,
                price=price,
                tags=tags,
                # Trimmed structured extract, not the HTML blob (recipe rule
                # for scraped sources): enough to debug a field-mapping issue.
                raw_payload=json.dumps(
                    {
                        "item_id": item_id,
                        "href": href,
                        "class": class_attr,
                        "excerpt_lines": excerpt_lines,
                        "occurrence_date": occ_date.isoformat(),
                    },
                    sort_keys=True,
                    default=str,
                ),
            )
        )
    return events


def parse_collection_page(html: str) -> list[Event]:
    """Parse one `/education-literature` list page into Events (pure function).

    Returns every kid-relevant, date-parseable occurrence on the page,
    UNFILTERED by date window — `fetch()` applies the window filter (same
    split as `bbg.parse_month_page`).
    """
    tree = HTMLParser(html)
    events: list[Event] = []
    for article in tree.css("article.BlogList-item"):
        try:
            events.extend(_parse_card(article))
        except Exception:  # noqa: BLE001
            logger.warning("kccny: failed to parse a card", exc_info=True)
            continue
    return events


def _next_page_url(html: str, current_url: str) -> str | None:
    """Find the "Older" pagination link's `?offset=` URL, or None on the last page."""
    tree = HTMLParser(html)
    for a in tree.css("nav.BlogList-pagination a.BlogList-pagination-link"):
        label = _clean(a.text()).lower()
        href = a.attributes.get("href")
        if href and "older" in label:
            return urljoin(current_url, href)
    return None


class KCCNYSource(Source):
    """Korean Cultural Center New York — /education-literature HTML scrape."""

    name = "kccny"
    display_name = "Korean Cultural Center NY"

    def __init__(
        self,
        *,
        collection_url: str = COLLECTION_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        page_delay: float = PAGE_DELAY_SECONDS,
        http_timeout: float = 30.0,
        max_pages: int = MAX_PAGES,
    ):
        self._collection_url = collection_url
        # Every run walks the full /education-literature collection ->
        # full-window re-fetch -> missing-detection eligible.
        self.window_days = window_days
        self._delay = page_delay
        self._timeout = http_timeout
        self._max_pages = max_pages

    def fetch(self) -> Iterable[Event]:
        today = datetime.now(NYC_TZ).date()
        horizon = today + timedelta(days=self.window_days)
        total = 0
        url: str | None = self._collection_url
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            for page_num in range(self._max_pages):
                if url is None:
                    break
                if page_num:
                    time_mod.sleep(self._delay)
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    html_text = resp.text
                except Exception:  # noqa: BLE001
                    logger.warning("kccny: failed to fetch %s", url, exc_info=True)
                    break
                for ev in parse_collection_page(html_text):
                    ev_date = ev.start_dt.astimezone(NYC_TZ).date()
                    if today <= ev_date <= horizon:
                        total += 1
                        yield ev
                url = _next_page_url(html_text, url)
        logger.info("kccny: yielded %d events", total)
