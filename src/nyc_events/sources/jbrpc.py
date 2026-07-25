"""Jamaica Bay-Rockaway Parks Conservancy (JBRPC) — jbrpc.org.

Nonprofit parks conservancy for Jamaica Bay and the Rockaway Peninsula,
running school programs, volunteer stewardship, and public events across a
mix of NPS (Gateway National Recreation Area — Floyd Bennett Field), NYS
Parks (Shirley Chisholm State Park), and NYC Parks (Dubos Point, Bayswater
City Park, Canarsie Pier, ...) properties. Confirmed by `source-verifier`
2026-07-25; full probe writeup and evidence in SOURCES-BACKLOG.md.

Platform: a real Squarespace Events collection (`<article
class="eventlist-event ...">` cards, collection id `5c735cba6e9a7f1fd2a76c5e`),
NOT a blog styled as events. `?format=json` (the Coney-Island-USA-style fast
path — confirmed live to return the expected `upcoming`/`past` shape with
`location.mapLat`/`mapLng`) is robots.txt-DISALLOWED here, same as `kccny` —
so this is a plain HTML scrape of `/events`, per that precedent. Losing the
JSON path means losing the free lat/lng; see "Borough inference" below.

Verified live 2026-07-25 (plain httpx + browser UA — no anti-bot on this
host; `curl_cffi impersonate="chrome"` hit a connection reset in the
source-verifier's sandbox, so plain httpx was used and worked fine).

No pagination needed: a single `GET /events` returns the ENTIRE forward
window in one page (~36 upcoming events out to ~140 days at probe time; the
JSON twin's `pagination` field only paginates the **past** list, which this
source doesn't need). `fetch()` still applies a `window_days`-bounded filter
client-side (default 60, the catalog's usual convention) rather than trusting
the full ~140-day page.

Kid-relevance strategy: category ALLOWLIST on "Kids and Families" — a real,
server-filed taxonomy field (confirmed both as an HTML chip link
`<a href="?category=Kids+and+Families">` AND in the JSON twin's `categories`
array — not just cosmetic), yielding ~68% of events (45/66 at probe time).
Plus the shared `ADULT_BLOCKLIST`/`ADULT_TITLE_BLOCKLIST`/`MEMBERS_ONLY` as a
hard-exclude safety net (checked first, before the category logic).

**Zero-category edge case — explicit decision.** Some clearly kid-relevant
cards ship with NO categories at all (`eventlist-cats` div absent), e.g. "9th
Annual Jamaica Bay Festival" and "Wheel Fun Rentals ... Patriotic LED Bike
Ride" in the fixture — both real, both family-friendly (their descriptions
say "family-friendly event" / "Families, couples, and friends can cruise...").
Dropping every uncategorized row would silently lose these. Decision: for a
card with an EMPTY categories list, fall back to an inclusive keyword check
on title+description (`family`, `families`, `kids`, `children`, `youth`,
`all ages`) instead of the category allowlist — same "search harder before
giving up" posture as the shared adult-blocklist safety net, scoped narrowly
to the zero-category case only (a card WITH categories that don't include
"Kids and Families" is still excluded outright, no keyword rescue — the
`Stewardship`/`Refresh`-only "Rockaway Refresh" cleanup series is real
programming this venue runs but isn't a kids event).

Recurrence: solved by Squarespace's URL scheme, no `id:date` composition
needed. Every card — including each occurrence of a recurring series like
"Rockaway Refresh" or "Stargazing at Floyd Bennett Field" — gets its own
unique detail URL via cumulatively-appended random slug suffixes (verified
across all 15 fixture cards: 15 distinct hrefs, zero collisions). `external_id`
is bound directly to the card's href (relative path); no date composition.

Precise times come from each card's "Add to Google Calendar" link, which
encodes exact UTC start/end (`dates=20260725T000000Z/20260725T013000Z`) —
regex-extracted for full datetime precision. Falls back to the coarse
`event-date` datetime attribute (date only) + the localized 12h start/end
time spans if the calendar link is ever absent/unparseable.

Borough inference — multi-site, NOT a single `SOURCE_NEIGHBORHOOD` constant.
Confirmed venues span both boroughs: Brooklyn (Floyd Bennett Field, Marine
Park, Shirley Chisholm State Park, Canarsie Pier, Plumb Beach, Mill Basin,
Gerritsen Ave Beach) and Queens/Rockaway (Jamaica Bay Wildlife Refuge, Dubos
Point, Bayswater City Park, Rockaway Community Park, Edgemere Farm, Jacob
Riis Park). A small static venue-label -> Borough lookup (`_VENUE_BOROUGH`,
matched by substring against the card's `eventlist-meta-address` label) plus
a numbered-"Beach ###"-street regex (Rockaway's numbered beach streets are
always Queens; Brooklyn's beaches are named — Gerritsen, Plumb, Manhattan
Beach — never numbered) covers everything seen in the fixture. Anything not
covered gets `borough=None` (e.g. "9th Annual Jamaica Bay Festival"'s "40+
Locations around Jamaica Bay and Rockaway" — genuinely multi-borough, no
single answer) and falls back to the enrich pass's Census forward-geocode
tier. Since the JSON path's free lat/lng is unavailable and several venues
are NPS/NYS (not NYC-Parks) properties, `park_neighborhoods.json` coverage is
not guaranteed — the geocode tier is expected to carry real weight here, more
than for a straight NYC-Parks-property source. `neighborhood` is left None by
this source per the usual split (enrich.py's job, not fetch()'s).

Missing-detection: opted IN (`window_days` set in `__init__`). The single
`/events` fetch is a genuine full-window re-fetch every run (not incremental
sitemap-lastmod discovery), so an in-window future event that disappears is
a real "upstream may have cancelled it" signal.

Price: no structured cost field. "free"-mentioning description text ->
Price.FREE; a "$" amount or "ticket" mention -> Price.PAID; otherwise
UNKNOWN (JBRPC's programming is overwhelmingly free community events).

Age: no structured field either; "ages 2-10"-style prose in the description
is regex-extracted when present (e.g. "Pollinator Explorers ... Designed for
children ages 2–10").
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, ADULT_TITLE_BLOCKLIST, MEMBERS_ONLY, contains_any, normalize
from .base import Source

logger = logging.getLogger(__name__)

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://www.jbrpc.org"
EVENTS_URL = BASE_URL + "/events"
DEFAULT_WINDOW_DAYS = 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

_KIDS_CATEGORY = "kids and families"

# Inclusive keyword fallback used ONLY for cards with an EMPTY categories list
# (see module docstring "Zero-category edge case"). A card that DOES carry
# categories but not "Kids and Families" is excluded outright — no keyword
# rescue for those.
_ZERO_CATEGORY_KEYWORDS: tuple[str, ...] = (
    "family", "families", "kids", "children", "youth", "all ages",
)

# Category (normalized) -> tag. "Kids and Families" itself doesn't need an
# entry — the default tag list already covers it.
_CATEGORY_TAGS: dict[str, str] = {
    "wildlife and nature": "nature",
    "active recreation": "sports",
    "history": "educational",
    "stewardship": "volunteer",
    "refresh": "volunteer",
}

_ZERO_CATEGORY_TAG_HINTS: tuple[tuple[str, str], ...] = (
    ("cleanup", "volunteer"),
    ("clean sweep", "volunteer"),
    ("stewardship", "volunteer"),
    ("wildlife", "nature"),
    ("nature", "nature"),
    ("pollinator", "nature"),
    ("bike", "sports"),
    ("kayak", "sports"),
    ("fishing", "sports"),
)

# Venue label (as printed in .eventlist-meta-address, BEFORE the "(map)"
# link) -> Borough. Matched by substring against the normalized label.
# Normalize() strips hyphens, so keys are written without them. Covers the
# ~12 recurring venue names named in SOURCES-BACKLOG.md's probe plus
# everything seen in the fixture (15/15 cards resolve via this table + the
# numbered-beach-street regex below). Anything unmatched -> borough=None,
# which falls back to the enrich pass's geocode tiers (see module docstring).
_VENUE_BOROUGH: tuple[tuple[str, Borough], ...] = (
    ("floyd bennett field", Borough.BROOKLYN),
    ("marine park", Borough.BROOKLYN),
    ("shirley chisholm state park", Borough.BROOKLYN),
    ("canarsie pier", Borough.BROOKLYN),
    ("plumb beach", Borough.BROOKLYN),
    ("mill basin", Borough.BROOKLYN),
    ("gerritsen", Borough.BROOKLYN),  # Gerritsen Ave Beach / Gerritsen Beach
    # The org's own office address (1750 Granville Payne Ave, East New York,
    # Brooklyn) shows up as a fallback venue label on some cards, e.g.
    # "Fishing Frenzy at Shirley Chisholm State Park" — both the org office
    # and that park are Brooklyn, so this key resolves correctly either way.
    ("jamaica bay rockaway parks conservancy", Borough.BROOKLYN),
    ("jamaica bay wildlife refuge", Borough.QUEENS),
    ("dubos point", Borough.QUEENS),
    ("bayswater city park", Borough.QUEENS),
    ("rockaway community park", Borough.QUEENS),
    ("edgemere farm", Borough.QUEENS),
    ("jacob riis", Borough.QUEENS),
    ("riis landing", Borough.QUEENS),
)

# Rockaway's numbered "Beach ###" streets are always Queens. Brooklyn's
# beaches are named (Gerritsen, Plumb, Manhattan Beach), never numbered, so
# this can't collide with the Brooklyn entries above.
_NUMBERED_BEACH_RX = re.compile(r"\bbeach\s*\d", re.IGNORECASE)

_AGE_RX = re.compile(r"ages?:?\s*(\d{1,2})\s*[–—-]\s*(\d{1,2})", re.IGNORECASE)
_GCAL_DATES_RX = re.compile(r"dates=(\d{8}T\d{6})Z/(\d{8}T\d{6})Z")
_LOCAL_TIME_RX = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.IGNORECASE)

_WS_RX = re.compile(r"\s+")


def _clean(node) -> str:
    if node is None:
        return ""
    return _WS_RX.sub(" ", node.text(separator=" ", strip=True)).strip()


def _clean_shallow(node) -> str:
    """Like _clean but only the node's own direct text (skips nested <a>).

    Used for the address li, whose only child is the "(map)" link — deep=False
    keeps just the venue-label text node.
    """
    if node is None:
        return ""
    return _WS_RX.sub(" ", node.text(separator=" ", strip=True, deep=False)).strip()


def _infer_borough(venue_label: str | None) -> Borough | None:
    if not venue_label:
        return None
    norm = normalize(venue_label)
    for needle, borough in _VENUE_BOROUGH:
        if needle in norm:
            return borough
    if _NUMBERED_BEACH_RX.search(norm):
        return Borough.QUEENS
    return None


def _is_kid_relevant(title: str, description: str, categories: list[str]) -> bool:
    combined = f"{title} {description}"
    if contains_any(combined, ADULT_BLOCKLIST):
        return False
    if contains_any(title, ADULT_TITLE_BLOCKLIST) or contains_any(title, MEMBERS_ONLY):
        return False
    if not categories:
        # See module docstring "Zero-category edge case".
        return contains_any(combined, _ZERO_CATEGORY_KEYWORDS)
    norm_cats = [normalize(c) for c in categories]
    return _KIDS_CATEGORY in norm_cats


def _infer_tags(title: str, description: str, categories: list[str]) -> list[str]:
    tags = ["family", "best for kids"]
    if categories:
        norm_cats = {normalize(c) for c in categories}
        for cat, tag in _CATEGORY_TAGS.items():
            if cat in norm_cats and tag not in tags:
                tags.append(tag)
    else:
        haystack = normalize(f"{title} {description}")
        for keyword, tag in _ZERO_CATEGORY_TAG_HINTS:
            if keyword in haystack and tag not in tags:
                tags.append(tag)
    return tags


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


def _parse_gcal_datetimes(gcal_href: str | None) -> tuple[datetime, datetime | None] | None:
    """Extract (start, end) UTC datetimes from the "Add to Google Calendar"
    link's `dates=<start>Z/<end>Z` query param, or None if absent/unparseable.
    """
    if not gcal_href:
        return None
    m = _GCAL_DATES_RX.search(gcal_href)
    if not m:
        return None
    start = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    end = datetime.strptime(m.group(2), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    if end <= start:
        end = None
    return start, end


def _to_24h(hour: int, minute: int, meridiem: str) -> tuple[int, int]:
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    return (hour if hour < 24 else 0), minute


def _parse_local_time(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    m = _LOCAL_TIME_RX.search(text)
    if not m:
        return None
    return _to_24h(int(m.group(1)), int(m.group(2)), m.group(3).upper())


def _parse_local_fallback(
    date_iso: str, start_text: str | None, end_text: str | None
) -> tuple[datetime, datetime | None]:
    """Build (start, end) from the date-only attribute + localized 12h time
    spans, in NYC local time. Used only when the Google Calendar link's UTC
    `dates=` param is absent or unparseable.
    """
    year, month, day = (int(p) for p in date_iso.split("-"))
    start_hm = _parse_local_time(start_text) or (0, 0)
    start_dt = datetime(year, month, day, start_hm[0], start_hm[1], tzinfo=NYC_TZ)
    end_dt = None
    end_hm = _parse_local_time(end_text)
    if end_hm is not None:
        end_dt = datetime(year, month, day, end_hm[0], end_hm[1], tzinfo=NYC_TZ)
        if end_dt <= start_dt:
            end_dt = None  # malformed prose — drop rather than invert
    return start_dt, end_dt


def _parse_card(article) -> Event | None:
    """Parse one `<article class="eventlist-event ...">` card, or None."""
    title_node = article.css_first(".eventlist-title-link")
    if title_node is None:
        return None
    title = _clean(title_node)
    href = title_node.attributes.get("href")
    if not title or not href:
        return None
    url = urljoin(BASE_URL, href)

    cats_node = article.css_first(".eventlist-cats")
    categories = [_clean(a) for a in (cats_node.css("a") if cats_node is not None else [])]

    desc_node = article.css_first(".eventlist-description .sqs-html-content")
    description = _clean(desc_node) or None

    if not _is_kid_relevant(title, description or "", categories):
        return None

    date_node = article.css_first("time.event-date")
    date_iso = date_node.attributes.get("datetime") if date_node is not None else None
    if not date_iso:
        logger.debug("jbrpc: skipping %r — no event-date datetime attribute", title)
        return None

    gcal_node = article.css_first("a.eventlist-meta-export-google")
    gcal_href = gcal_node.attributes.get("href") if gcal_node is not None else None
    parsed = _parse_gcal_datetimes(gcal_href)
    if parsed is not None:
        start_dt, end_dt = parsed
    else:
        start_text = _clean(article.css_first("time.event-time-localized-start")) or None
        end_text = _clean(article.css_first("time.event-time-localized-end")) or None
        try:
            start_dt, end_dt = _parse_local_fallback(date_iso, start_text, end_text)
        except ValueError:
            logger.debug("jbrpc: skipping %r — unparseable date %r", title, date_iso)
            return None

    addr_node = article.css_first(".eventlist-meta-address")
    venue_label = _clean_shallow(addr_node) or None
    borough = _infer_borough(venue_label)

    age_min, age_max = _parse_age_range(description or "")
    price = _parse_price(description or "")
    tags = _infer_tags(title, description or "", categories)

    return Event(
        id=compute_id(
            "jbrpc",
            external_id=href,
            url=url,
            title=title,
            venue=venue_label,
            date_iso=date_iso,
        ),
        source="jbrpc",
        external_id=href,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=venue_label,
        borough=borough,
        lat=None,
        lng=None,
        age_min=age_min,
        age_max=age_max,
        price=price,
        tags=tags,
        # Trimmed structured extract, not the HTML blob (recipe rule for
        # scraped sources): enough to debug a field-mapping question.
        raw_payload=json.dumps(
            {
                "href": href,
                "categories": categories,
                "venue_label": venue_label,
                "date_iso": date_iso,
                "gcal_href": gcal_href,
                "description": description,
            },
            sort_keys=True,
            default=str,
        ),
    )


def parse_events_page(html: str) -> list[Event]:
    """Parse the /events list page into Events (pure function).

    Includes cards from BOTH the eventlist--upcoming and eventlist--past
    sections (the page renders them together) — `fetch()` applies the
    date-window filter, so past cards are naturally dropped there.
    """
    tree = HTMLParser(html)
    events: list[Event] = []
    for article in tree.css("article.eventlist-event"):
        try:
            ev = _parse_card(article)
        except Exception:  # noqa: BLE001
            logger.warning("jbrpc: failed to parse a card", exc_info=True)
            continue
        if ev is not None:
            events.append(ev)
    return events


class JBRPCSource(Source):
    """Jamaica Bay-Rockaway Parks Conservancy — /events HTML scrape (Squarespace)."""

    name = "jbrpc"
    display_name = "Jamaica Bay-Rockaway Parks Conservancy"

    def __init__(
        self,
        *,
        events_url: str = EVENTS_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        http_timeout: float = 30.0,
    ):
        self._events_url = events_url
        # A single GET /events returns the entire forward window in one page
        # (no pagination on the upcoming list) — a real full-window re-fetch
        # every run, so missing-detection is opted IN.
        self.window_days = window_days
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
            try:
                resp = client.get(self._events_url)
                resp.raise_for_status()
                html_text = resp.text
            except Exception:  # noqa: BLE001
                logger.warning("jbrpc: failed to fetch %s", self._events_url, exc_info=True)
                return
        for ev in parse_events_page(html_text):
            ev_date = ev.start_dt.astimezone(NYC_TZ).date()
            if today <= ev_date <= horizon:
                total += 1
                yield ev
        logger.info("jbrpc: yielded %d events", total)
