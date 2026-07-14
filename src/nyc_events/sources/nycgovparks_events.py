"""NYC Parks website events — the "Best for Kids" category on nycgovparks.org.

The live NYC Parks events calendar (https://www.nycgovparks.org/events) is a
separate, actively-maintained system from the frozen Open Data export
(`fudw-fgrp`, dead since 2019-12) that originally sent Phase 1 to the permit
registry (`tvpp-9vvx`). Overlap with the permit source is effectively zero
(verified 2026-07-06: same-day exact/fuzzy title intersection was empty), so
this source runs *alongside* it, no dedup needed. See the as-built notes in
SOURCES-BACKLOG.md ("Major reassessment: nycgovparks.org/events").

Data flow:
  1. GET https://www.nycgovparks.org/events/kids (server-rendered HTML; the
     category is NYC Parks' own curated "Best for Kids" tag, cat_id 18). Plain
     httpx + a browser User-Agent — no anti-bot on this host (curl_cffi is NOT
     needed and has shown connection resets where httpx succeeds).
  2. Page 1 embeds `var eventsByLocationJSON = [...]` — a map-widget blob
     covering the ENTIRE server window (not just page 1's 50 cards): venues
     with `lat`/`lng`/`borough` and per-occurrence event `link` paths. Parse
     it once and join by the card's detail-URL path → coordinates + the
     parent-venue name come free; this source needs zero geocoding.
  3. Parse the schema.org Event MICRODATA cards
     (`itemscope itemtype="http://schema.org/Event"`) with selectolax.
  4. Paginate /events/kids/p2, /p3, … until a page yields 0 cards (~49 pages;
     the terminator is an HTTP-200 page with 0 cards, NOT a 404). 1s polite
     delay between pages.

Verified live 2026-07-06 (record of the external_id check, per the recipe):
  - IDs are PER-OCCURRENCE — recurring programs get a distinct numeric id AND
    a distinct dated URL for every occurrence (Kids in Motion @ Anne Loftus
    Playground: 2026-07-07 = id 2192210 at /events/2026/07/07/…, 2026-07-09 =
    id 2192170 at /events/2026/07/09/…). The numeric id from
    `<h3 id="event_title__<id>">` is therefore used as `external_id` as-is;
    no permit-style `:start_dt` suffix is needed.
  - `meta itemprop="startDate"` is full ISO-8601 WITH offset
    ("2026-07-06T08:00:00-04:00") — `datetime.fromisoformat` directly, no
    wall-time ambiguity.
  - The blob's top-level venue name is the park PROPERTY ("Tudor Park" for a
    card whose microdata Place is "Addabbo Playground (in Tudor Park)";
    "Alfred E. Smith Recreation Center" for its "Multi-Use Room"). We prefer
    it — it lines up with the `park_neighborhoods.json` enrich tier. Fallback
    when a link isn't in the blob: the "(in <parent>)" text, then the Place
    name.
  - Titles prefixed "CANCELLED:" appear in the feed — skipped at parse time
    (explicit upstream cancellation beats our possibly_cancelled heuristic).
  - Category ids ride each card's class list ("row event cat18 cat205 …");
    the id→tag table below was resolved live 2026-07-06 by intersecting card
    class-ids across /events + per-category pages. Unknown ids are skipped.

Filtering: NONE — this is NYC Parks' own kid-curated category (same stance as
mommy_poppins / bk_childrens_museum). The shared ADULT_BLOCKLIST is applied
only as a cheap safety net.

Full-window source: every fetch re-lists the whole server window (today →
end of next month, ~55–61 days), so it opts IN to missing-event detection
with the conservative lower bound (window_days=55).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from datetime import datetime
from typing import NamedTuple

import httpx
from selectolax.parser import HTMLParser, Node

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, ADULT_TITLE_BLOCKLIST, contains_any
from .base import Source

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nycgovparks.org"
EVENTS_URL = f"{BASE_URL}/events/kids"
# Server window is "today → end of next month" (~55-61 days depending on the
# calendar); use the conservative lower bound for missing-detection.
DEFAULT_WINDOW_DAYS = 55
PAGE_DELAY_SECONDS = 1.0
# Safety cap so a runaway loop can't hammer the site (~49 pages at capture).
MAX_PAGES = 80
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# The map-widget blob on page 1. Covers the ENTIRE current window; PHP-style
# \/ escaping is transparent to json.loads.
_BLOB_RX = re.compile(r"var eventsByLocationJSON = (\[.*?\]);", re.S)

_EVENT_ID_RX = re.compile(r"event_title__(\d+)")
_PARENT_VENUE_RX = re.compile(r"\(in ([^)]+)\)")
_TIME_LIKE_RX = re.compile(r"\d{1,2}:\d{2}")

_BOROUGH_MAP = {
    "manhattan": Borough.MANHATTAN,
    "brooklyn": Borough.BROOKLYN,
    "queens": Borough.QUEENS,
    "bronx": Borough.BRONX,
    "the bronx": Borough.BRONX,
    "staten island": Borough.STATEN_ISLAND,
}

# NYC Parks category id -> our tag. Ids resolved live 2026-07-06 by
# intersecting the `catNN` class lists of cards on /events (which shows a
# "Category:" link line) and on per-category pages (/events/<slug>). The slug
# is noted per row. Ids not listed here (e.g. 122 seniors, 205
# recreation-centers, 211/206/291 internal markers) are deliberately
# unmapped — audience/venue-type markers, not activities.
_CATEGORY_TAGS: dict[int, str] = {
    2: "arts & crafts",  # arts-and-crafts
    4: "nature",  # birding
    5: "educational",  # education
    7: "music",  # concerts
    9: "dance",  # dance
    10: "nature",  # nature
    11: "art",  # exhibits ("Art")
    12: "festival",  # festivals
    13: "movie",  # film
    14: "fitness",  # fitness
    15: "games",  # games
    17: "history",  # history
    18: "best for kids",  # kids ("Best for Kids")
    20: "market",  # markets
    23: "pets",  # pets
    25: "sports",  # sports
    27: "theater",  # theater
    28: "tour",  # tours
    29: "volunteer",  # volunteer
    47: "nature",  # urbanparkrangers
    100: "food",  # food
    102: "waterfront",  # kayaking
    105: "fitness",  # shape-up-nyc
    106: "educational",  # talks
    109: "waterfront",  # waterfront
    121: "fitness",  # outdoor-fitness
    125: "science",  # astronomy
    128: "fishing",  # fishing
    137: "sports",  # summer-sports-experience
    147: "nature",  # hiking
    167: "nature",  # wildlife
    303: "gardening",  # gardening
}


class MapVenue(NamedTuple):
    """One blob venue, joined to cards by per-occurrence link path."""

    name: str | None
    borough: str | None
    lat: float | None
    lng: float | None


# --- Pure helper functions (testable without network) ------------------------


def _to_float(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_location_blob(html: str) -> dict[str, MapVenue]:
    """Extract the eventsByLocationJSON blob into {link_path: MapVenue}.

    The blob on page 1 covers the entire window, so calling this once is
    enough. Returns {} (and logs) when the blob is missing/unparseable —
    cards then just lose lat/lng and fall back to microdata venue names.
    """
    m = _BLOB_RX.search(html)
    if not m:
        logger.warning("nycgovparks_events: eventsByLocationJSON blob not found")
        return {}
    try:
        venues = json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.warning("nycgovparks_events: eventsByLocationJSON blob unparseable")
        return {}

    link_map: dict[str, MapVenue] = {}
    for venue in venues:
        try:
            info = MapVenue(
                name=(venue.get("name") or "").strip() or None,
                borough=(venue.get("borough") or "").strip() or None,
                lat=_to_float(venue.get("lat")),
                lng=_to_float(venue.get("lng")),
            )
            for location in venue.get("locations") or []:
                for ev in location.get("events") or []:
                    link = ev.get("link")
                    if link:
                        link_map[link] = info
        except Exception:  # noqa: BLE001
            logger.warning(
                "nycgovparks_events: failed to parse blob venue %r",
                venue.get("name") if isinstance(venue, dict) else venue,
                exc_info=True,
            )
    return link_map


def _meta_content(card: Node, itemprop: str) -> str | None:
    node = card.css_first(f'meta[itemprop="{itemprop}"]')
    if node is None:
        return None
    return (node.attributes.get("content") or "").strip() or None


def _parse_iso_dt(raw: str | None) -> datetime | None:
    """Parse the card's ISO-8601-with-offset meta datetime."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _extract_cost_text(card: Node) -> str | None:
    """The cost line is a <strong> that isn't one of the HH:MM time strongs."""
    for strong in card.css("strong"):
        text = strong.text(strip=True)
        if not text or _TIME_LIKE_RX.search(text) or text.startswith("Category"):
            continue
        return text
    return None


def _infer_tags(cat_ids: set[int]) -> list[str]:
    """Map the card's NYC Parks category ids to our tag vocabulary.

    The whole feed is the Parks-curated kids category, so "family" and
    "best for kids" are seeded unconditionally (cat18's mapping is then a
    harmless no-op).
    """
    tags = ["family", "best for kids"]
    for cid in sorted(cat_ids):
        tag = _CATEGORY_TAGS.get(cid)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _parse_card(card: Node, link_map: dict[str, MapVenue]) -> Event | None:
    """Parse one schema.org Event microdata card into an Event, or None.

    Returns None for rows that should be skipped: CANCELLED: titles, the
    ADULT_BLOCKLIST safety net, or cards missing a usable title/id/date.
    """
    h3 = card.css_first("h3.event-title")
    anchor = h3.css_first("a[href]") if h3 is not None else None
    if h3 is None or anchor is None:
        logger.debug("nycgovparks_events: card without title anchor — skipped")
        return None

    title = anchor.text(strip=True)
    if not title:
        return None
    # Explicit upstream cancellation (observed live) — beats our
    # possibly_cancelled heuristic; just drop the row.
    if title.lower().startswith("cancelled"):
        logger.debug("nycgovparks_events: skipping cancelled row %r", title)
        return None

    id_match = _EVENT_ID_RX.search(h3.attributes.get("id") or "")
    external_id = id_match.group(1) if id_match else None

    href = (anchor.attributes.get("href") or "").strip()
    url = f"{BASE_URL}{href}" if href.startswith("/") else (href or None)

    start_raw = _meta_content(card, "startDate")
    start_dt = _parse_iso_dt(start_raw)
    if start_dt is None:
        logger.debug("nycgovparks_events: skipping %r — no parseable startDate", title)
        return None
    end_raw = _meta_content(card, "endDate")
    end_dt = _parse_iso_dt(end_raw)

    place_node = card.css_first('[itemprop="location"] [itemprop="name"]')
    place_name = place_node.text(strip=True) if place_node is not None else None

    location_node = card.css_first('[itemprop="location"]')
    parent_match = (
        _PARENT_VENUE_RX.search(location_node.text()) if location_node is not None else None
    )
    parent_venue = parent_match.group(1).strip() if parent_match else None

    street_address = _meta_content(card, "streetAddress")
    locality_node = card.css_first('[itemprop="addressLocality"]')
    locality = locality_node.text(strip=True) if locality_node is not None else None

    desc_node = card.css_first('[itemprop="description"]')
    description = desc_node.text(strip=True) if desc_node is not None else None

    # Safety net only — the category itself is Parks-curated kids content.
    haystack = f"{title} {description or ''}"
    if contains_any(haystack, ADULT_BLOCKLIST) or contains_any(title, ADULT_TITLE_BLOCKLIST):
        logger.debug("nycgovparks_events: adult-blocklist hit, skipping %r", title)
        return None

    cost_text = _extract_cost_text(card)
    price = Price.FREE if cost_text and cost_text.lower().startswith("free") else Price.UNKNOWN

    cat_ids = {
        int(cls[3:])
        for cls in (card.attributes.get("class") or "").split()
        if cls.startswith("cat") and cls[3:].isdigit()
    }
    tags = _infer_tags(cat_ids)

    accessible = any(
        "accessible" in (img.attributes.get("src") or "") for img in card.css("img")
    )
    pearls_pick = card.css_first(".pearls-pick-box") is not None

    # Blob join by detail-URL path: lat/lng + the park-property venue name.
    map_venue = link_map.get(href)
    # Prefer the blob's top-level (park-property) name; fall back to the
    # "(in <parent>)" text, then the microdata Place (sub-room) name.
    venue_name = (map_venue.name if map_venue else None) or parent_venue or place_name

    borough = _BOROUGH_MAP.get((locality or "").lower())
    if borough is None and map_venue and map_venue.borough:
        borough = _BOROUGH_MAP.get(map_venue.borough.lower())

    # Trimmed structured extract, NOT the HTML blob (see the HTML-source
    # raw_payload convention).
    raw: dict[str, object] = {
        "event_id": external_id,
        "title": title,
        "link": href,
        "start": start_raw,
        "end": end_raw,
        "place_name": place_name,
        "parent_venue": parent_venue,
        "street_address": street_address,
        "address_locality": locality,
        "description_snippet": description,
        "cost_text": cost_text,
        "category_ids": sorted(cat_ids),
        "accessible": accessible,
        "pearls_pick": pearls_pick,
    }
    if map_venue is not None:
        raw["map_venue"] = map_venue._asdict()

    return Event(
        id=compute_id("nycgovparks_events", external_id=external_id, url=url, title=title),
        source="nycgovparks_events",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=venue_name,
        borough=borough,
        neighborhood=None,  # enrich pass codes it (lat/lng arrives with the row)
        lat=map_venue.lat if map_venue else None,
        lng=map_venue.lng if map_venue else None,
        age_min=None,
        age_max=None,
        price=price,
        tags=tags,
        raw_payload=json.dumps(raw, sort_keys=True, default=str),
    )


def parse_page(html: str, link_map: dict[str, MapVenue]) -> tuple[list[Event], int]:
    """Parse one list page. Returns (events, card_count).

    card_count is the number of microdata Event cards on the page — the
    pagination terminator (a page past the end is HTTP 200 with 0 cards, not
    a 404), which must count skipped rows too.
    """
    tree = HTMLParser(html)
    cards = tree.css('[itemtype="http://schema.org/Event"]')
    events: list[Event] = []
    for card in cards:
        try:
            ev = _parse_card(card, link_map)
        except Exception:  # noqa: BLE001
            logger.warning("nycgovparks_events: failed to parse a card", exc_info=True)
            continue
        if ev is not None:
            events.append(ev)
    return events, len(cards)


class NYCGovParksEventsSource(Source):
    """NYC Parks 'Best for Kids' calendar (microdata scrape + in-page map blob)."""

    name = "nycgovparks_events"
    display_name = "NYC Parks"

    def __init__(
        self,
        *,
        events_url: str = EVENTS_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        page_delay: float = PAGE_DELAY_SECONDS,
        http_timeout: float = 30.0,
        max_pages: int = MAX_PAGES,
    ):
        self._events_url = events_url
        # Full-window re-fetch every run → missing-detection eligible.
        self.window_days = window_days
        self._page_delay = page_delay
        self._timeout = http_timeout
        self._max_pages = max_pages

    def fetch(self) -> Iterable[Event]:
        """Paginate /events/kids, yielding Events until an empty page."""
        html = self._get_page(1)
        if html is None:
            logger.warning("nycgovparks_events: page 1 fetch failed — aborting run")
            return

        # Page 1's blob covers the whole window; parse it once.
        link_map = parse_location_blob(html)

        count = 0
        page = 1
        while True:
            events, n_cards = parse_page(html, link_map)
            if n_cards == 0:
                break  # HTTP-200 empty page = end of the window
            for ev in events:
                count += 1
                yield ev

            page += 1
            if page > self._max_pages:
                logger.warning(
                    "nycgovparks_events: hit max_pages=%d — stopping", self._max_pages
                )
                break
            time.sleep(self._page_delay)
            html = self._get_page(page)
            if html is None:
                # Soft-fail mid-run: stop paginating. ingest's circuit breaker
                # (_fetch_looks_complete) guards against a half-empty fetch
                # mass-flagging the source.
                break

        logger.info("nycgovparks_events: yielded %d events across %d pages", count, page - 1)

    def _get_page(self, page: int) -> str | None:
        """Fetch one list page; returns None on failure (soft-fail)."""
        url = self._events_url if page == 1 else f"{self._events_url}/p{page}"
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError:
            logger.warning("nycgovparks_events: failed to fetch %s", url, exc_info=True)
            return None
