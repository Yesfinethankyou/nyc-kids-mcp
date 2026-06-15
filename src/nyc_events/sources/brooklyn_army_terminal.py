"""Brooklyn Army Terminal events.

The Brooklyn Army Terminal (BAT) is an NYCEDC-managed waterfront campus in
Sunset Park, Brooklyn. Its public events page mixes two very different things:
free community/family programming ("Summer at the Terminal" markets, food
fests, Community and Family Day, cultural festivals, Rooftop Films) and
ticketed 21+ EDM nightclub concerts on Pier 4 (titled "Live Music Concert
with <promoter>", sold via dice.fm / posh.vip). We keep the former and drop
the latter — see `_is_kid_relevant`.

Data flow:
  1. GET https://brooklynarmyterminal.com/events (curl_cffi with Chrome
     impersonation — Cloudflare blocks plain fetchers; the `www.` host 403s,
     use the non-www host). Single server-rendered page, no pagination.
  2. Parse `.events-full-width__grid-card` cards with selectolax.
  3. Drop any card whose title starts with "Live Music Concert".
  4. Build start_dt from the card's day/month/year + start of the time range.
  5. Yield Event objects.

Quirks (verified live, 2026-06-15):
  - No upstream per-event id and no per-event detail URL on most cards.
    `external_id` is left None so compute_id falls back to title|venue|date.
    The card's `<a href>` is an external link (dice.fm, posh.vip, the
    Rooftop Films calendar, a Facebook page, artbuilt.org) when present,
    None otherwise.
  - This is a FULL-WINDOW re-fetch: the single page lists every upcoming
    event, so it opts into missing-event detection (window_days=60). 24
    cards on the captured page span June–October 2026.
  - The `time` field is a range like "1:00-7:00pm" or "10:00am-2:00pm";
    am/pm may be omitted on the start. We parse the START time only. When
    am/pm is missing on the start we borrow it from the end of the range;
    if the whole time is unparseable the event becomes an all-day 00:00.
  - Card subtitles can embed Cloudflare-obfuscated email spans
    (`<span class="__cf_email__">[email protected]</span>`); we drop those
    spans rather than render the placeholder text.
  - Kept events are free community programming → Price.FREE, unless the
    card links to a ticketing host (dice.fm / posh.vip) → Price.PAID. After
    filtering out "Live Music Concert", no kept card on the captured page
    links to a ticketing host, but the rule is kept defensively.
  - Times are naive local (America/New_York); we store them as naive (no
    tz attached), matching the page's wall-clock semantics.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime

from curl_cffi import requests as cffi_requests
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

EVENTS_URL = "https://brooklynarmyterminal.com/events"
VENUE_NAME = "Brooklyn Army Terminal"
DEFAULT_WINDOW_DAYS = 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# Titles starting with this are the 21+ EDM nightclub concerts on Pier 4
# (sold via dice.fm / posh.vip). Not kid-relevant — dropped.
_DROP_TITLE_PREFIX = "live music concert"

# Hosts that indicate a ticketed (paid) event. Kept events that link here
# are treated as PAID; everything else kept is free community programming.
_PAID_LINK_HOSTS = ("dice.fm", "posh.vip")

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_WS_RX = re.compile(r"\s+")

# Matches a clock time with optional am/pm, e.g. "1:00", "10:00am", "7:00pm".
_TIME_RX = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Tag inference (title keyword driven — no upstream categories)
# ---------------------------------------------------------------------------

_TITLE_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("market", ("market", "sip and stroll", "food fest", "ferry food")),
    ("movie", ("cinema", "film", "screening", "rooftop films")),
    ("music", ("salsa", "music", "concert")),
    ("wellness", ("wellness", "yoga")),
    ("cultural", ("latin", "asian", "hispanic", "heritage", "muertos", "culture")),
    ("best for kids", ("family", "community and family", "kids")),
]


def _clean_text(node) -> str:
    """Extract visible text from a node, dropping Cloudflare-obfuscated email spans."""
    if node is None:
        return ""
    # Remove obfuscated-email placeholder spans before reading text.
    for span in node.css(".__cf_email__"):
        span.decompose()
    text = node.text(separator=" ", strip=True)
    return _WS_RX.sub(" ", text).strip()


def _parse_start_time(time_str: str) -> tuple[int, int]:
    """Parse the START of a time range like '1:00-7:00pm' into (hour24, minute).

    am/pm may be omitted on the start; borrow it from the end of the range
    when needed. Returns (0, 0) if nothing parseable (treated as all-day).
    """
    if not time_str:
        return 0, 0
    matches = list(_TIME_RX.finditer(time_str))
    # Keep only matches that actually captured a number (the regex can match
    # an empty meridiem-only fragment).
    matches = [m for m in matches if m.group(1)]
    if not matches:
        return 0, 0

    start = matches[0]
    hour = int(start.group(1))
    minute = int(start.group(2) or 0)
    meridiem = (start.group(3) or "").lower().replace(".", "")

    if not meridiem and len(matches) > 1:
        # Borrow am/pm from the end of the range, if it has one.
        meridiem = (matches[-1].group(3) or "").lower().replace(".", "")

    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23:
        hour = 0
    return hour, minute


def _resolve_price(url: str | None) -> Price:
    """Free community programming unless the card links to a ticketing host."""
    if url and any(host in url for host in _PAID_LINK_HOSTS):
        return Price.PAID
    return Price.FREE


def _infer_tags(title: str) -> list[str]:
    """Infer tags from title keywords."""
    tags: list[str] = ["family"]
    title_lower = title.lower()
    for tag, keywords in _TITLE_TAG_RULES:
        if tag not in tags and any(kw in title_lower for kw in keywords):
            tags.append(tag)
    return tags


def _is_kid_relevant(title: str) -> bool:
    """Drop the 21+ EDM nightclub concerts; keep community programming."""
    return not title.strip().lower().startswith(_DROP_TITLE_PREFIX)


def _parse_card(card) -> Event | None:
    """Parse one `.events-full-width__grid-card` node into an Event, or None.

    Returns None when the card is filtered out (Live Music Concert) or lacks
    a usable title/date.
    """
    title_node = card.css_first(".card__title")
    title = _clean_text(title_node)
    if not title:
        return None

    if not _is_kid_relevant(title):
        return None

    day_node = card.css_first(".day")
    month_node = card.css_first(".month")
    year_node = card.css_first(".year")
    if not (day_node and month_node and year_node):
        logger.debug("brooklyn_army_terminal: skipping %r — incomplete date", title)
        return None

    try:
        day = int(_clean_text(day_node))
        month = _MONTHS[_clean_text(month_node).lower()]
        year = int(_clean_text(year_node))
    except (ValueError, KeyError):
        logger.debug("brooklyn_army_terminal: skipping %r — unparseable date", title)
        return None

    time_node = card.css_first(".time")
    hour, minute = _parse_start_time(_clean_text(time_node))

    try:
        start_dt = datetime(year, month, day, hour, minute)
    except ValueError:
        logger.debug("brooklyn_army_terminal: skipping %r — invalid date components", title)
        return None

    link_node = card.css_first("a[href]")
    url = (link_node.attributes.get("href") if link_node else None) or None

    subtitle = _clean_text(card.css_first(".card__subtitle")) or None

    price = _resolve_price(url)
    tags = _infer_tags(title)

    return Event(
        id=compute_id(
            "brooklyn_army_terminal",
            title=title,
            venue=VENUE_NAME,
            date_iso=start_dt.date().isoformat(),
        ),
        source="brooklyn_army_terminal",
        external_id=None,
        title=title,
        description=subtitle,
        url=url,
        start_dt=start_dt,
        end_dt=None,
        venue_name=VENUE_NAME,
        borough=Borough.BROOKLYN,
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=price,
        tags=tags,
    )


def parse_events(html: str) -> list[Event]:
    """Parse the BAT events page HTML into a list of Events (pure function)."""
    tree = HTMLParser(html)
    events: list[Event] = []
    for card in tree.css(".events-full-width__grid-card"):
        try:
            ev = _parse_card(card)
        except Exception:  # noqa: BLE001
            logger.warning("brooklyn_army_terminal: failed to parse a card", exc_info=True)
            continue
        if ev is not None:
            events.append(ev)
    return events


class BrooklynArmyTerminalSource(Source):
    """Brooklyn Army Terminal community/family events (single-page HTML scrape)."""

    name = "brooklyn_army_terminal"

    def __init__(
        self,
        *,
        events_url: str = EVENTS_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        http_timeout: float = 30.0,
    ):
        self._events_url = events_url
        # Full-window single-page re-fetch every run → missing-detection eligible.
        self.window_days = window_days
        self._timeout = http_timeout

    def fetch(self) -> Iterable[Event]:
        """Fetch and parse the single events page, yielding kid-relevant Events."""
        try:
            resp = cffi_requests.get(
                self._events_url,
                headers={"User-Agent": USER_AGENT},
                impersonate="chrome",
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except Exception:  # noqa: BLE001
            logger.warning("brooklyn_army_terminal: failed to fetch events page", exc_info=True)
            return

        events = parse_events(resp.text)
        logger.info("brooklyn_army_terminal: yielded %d events", len(events))
        yield from events
