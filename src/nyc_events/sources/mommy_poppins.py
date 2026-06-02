"""Mommy Poppins NYC family events.

Phase 2 editorial source. Discovers NYC event URLs via sitemap XML, then
scrapes detail pages for JSON-LD + drupalSettings structured data.

Data flow:
  1. Fetch sitemap index -> list of sitemap page URLs
  2. Fetch each sitemap page, filter for /new-york-city-kids/event/ URLs
     with recent lastmod
  3. Fetch each detail page HTML
  4. Extract JSON-LD (@type: Event) for structured fields
  5. Extract drupalSettings for coordinates / node ID
  6. Parse age range and price from HTML body text as fallback
  7. Yield Event objects

Rate limiting: 1.5s delay between requests, custom User-Agent.
Expected yield: ~100-200 NYC events per run.
"""

from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from curl_cffi import requests as cffi_requests
from selectolax.parser import HTMLParser

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

SITEMAP_INDEX_URL = "https://mommypoppins.com/sitemap.xml"
NYC_EVENT_URL_PREFIX = "https://mommypoppins.com/new-york-city-kids/event/"
DEFAULT_LOOKBACK_DAYS = 90
REQUEST_DELAY_SECONDS = 1.5
# Sitemap XML namespace
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# --- Borough inference from coordinates ---

# Rough bounding boxes (lat_min, lat_max, lng_min, lng_max) for each borough.
# These are intentionally generous — false positives at borough boundaries
# are acceptable; the alternative (geocoding API) is overkill for this.
_BOROUGH_BOXES: list[tuple[Borough, float, float, float, float]] = [
    (Borough.MANHATTAN,     40.700, 40.882, -74.020, -73.907),
    (Borough.BROOKLYN,      40.570, 40.739, -74.042, -73.855),
    (Borough.QUEENS,        40.541, 40.812, -73.962, -73.700),
    (Borough.BRONX,         40.785, 40.917, -73.934, -73.748),
    (Borough.STATEN_ISLAND, 40.496, 40.651, -74.255, -74.052),
]

# Known venue name -> borough for cases where coords are missing
_VENUE_BOROUGH_LOOKUP: dict[str, Borough] = {
    "central park": Borough.MANHATTAN,
    "prospect park": Borough.BROOKLYN,
    "brooklyn botanic garden": Borough.BROOKLYN,
    "brooklyn children's museum": Borough.BROOKLYN,
    "brooklyn museum": Borough.BROOKLYN,
    "brooklyn public library": Borough.BROOKLYN,
    "new york hall of science": Borough.QUEENS,
    "queens botanical garden": Borough.QUEENS,
    "queens museum": Borough.QUEENS,
    "bronx zoo": Borough.BRONX,
    "new york botanical garden": Borough.BRONX,
    "wave hill": Borough.BRONX,
    "staten island children's museum": Borough.STATEN_ISLAND,
    "snug harbor": Borough.STATEN_ISLAND,
    "lincoln center": Borough.MANHATTAN,
    "american museum of natural history": Borough.MANHATTAN,
    "intrepid sea, air & space museum": Borough.MANHATTAN,
    "children's museum of manhattan": Borough.MANHATTAN,
    "hudson yards": Borough.MANHATTAN,
    "bryant park": Borough.MANHATTAN,
    "union square": Borough.MANHATTAN,
    "madison square park": Borough.MANHATTAN,
    "battery park": Borough.MANHATTAN,
    "flushing meadows": Borough.QUEENS,
    "coney island": Borough.BROOKLYN,
    "astoria park": Borough.QUEENS,
}

# Kid-relevant keyword -> tag mapping (shared concept with nyc_permitted_events
# but tuned for editorial content which has richer descriptions).
_KID_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("story time", ("story time", "storytime", "story hour", "read aloud", "reading")),
    ("family", ("family", "families", "all ages", "intergenerational")),
    ("arts & crafts", (
        "craft", "art workshop", "paint", "draw", "make ", "diy", "art class", "art",
    )),
    ("nature", ("nature", "garden", "outdoor", "wildlife", "birding", "park", "zoo", "botanical")),
    ("music", ("music", "concert", "sing", "dance", "drum", "performance", "recital")),
    ("sports", ("sports", "soccer", "baseball", "basketball", "swimming", "yoga", "skating")),
    ("educational", (
        "workshop", "stem", "science", "history", "lesson", "class",
        "museum", "learning", "educational",
    )),
    ("festival", ("festival", "fair", "block party", "celebration", "carnival")),
    ("best for kids", ("kid", "child", "tot", "toddler", "preschool", "youth", "baby", "infant")),
    ("movie", ("movie", "film", "screening", "cinema")),
    ("theater", ("theater", "theatre", "puppet", "show", "play", "musical")),
    ("free", ("free",)),
]

# Regex for extracting age range from page text
_AGE_RANGE_RX = re.compile(
    r"(?:age[s]?[:\s]+|for\s+(?:ages?\s+)?)"
    r"(\d{1,2})\s*[-–to]+\s*(\d{1,2})",
    re.IGNORECASE,
)
_AGE_ALL_RX = re.compile(r"\ball\s+ages\b", re.IGNORECASE)
_AGE_PLUS_RX = re.compile(r"(?:age[s]?[:\s]+)(\d{1,2})\+", re.IGNORECASE)

# nid extraction from googletag setTargeting call
_NID_RX = re.compile(r"setTargeting\('nid',\s*'(\d+)'\)")


# --- Pure helper functions (testable without network) ---


def _parse_sitemap_index(xml_text: str) -> list[str]:
    """Extract sitemap page URLs from a sitemap index XML."""
    root = ET.fromstring(xml_text)  # noqa: S314 — trusted source
    urls: list[str] = []
    # Try sitemapindex format first
    for sitemap in root.findall("sm:sitemap", _SITEMAP_NS):
        loc = sitemap.find("sm:loc", _SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    # If no sitemapindex entries, the root might be a urlset itself
    if not urls:
        for url_el in root.findall("sm:url", _SITEMAP_NS):
            loc = url_el.find("sm:loc", _SITEMAP_NS)
            if loc is not None and loc.text:
                urls.append(loc.text.strip())
    return urls


def _parse_sitemap_page(
    xml_text: str,
    url_prefix: str = NYC_EVENT_URL_PREFIX,
    min_lastmod: datetime | None = None,
) -> list[str]:
    """Extract event URLs from a sitemap page, filtered by prefix and recency."""
    root = ET.fromstring(xml_text)  # noqa: S314 — trusted source
    urls: list[str] = []
    for url_el in root.findall("sm:url", _SITEMAP_NS):
        loc = url_el.find("sm:loc", _SITEMAP_NS)
        if loc is None or not loc.text:
            continue
        loc_text = loc.text.strip()
        if not loc_text.startswith(url_prefix):
            continue
        if min_lastmod is not None:
            lastmod_el = url_el.find("sm:lastmod", _SITEMAP_NS)
            if lastmod_el is not None and lastmod_el.text:
                try:
                    lastmod = datetime.fromisoformat(lastmod_el.text.strip())
                    if lastmod.tzinfo is None:
                        lastmod = lastmod.replace(tzinfo=UTC)
                    if lastmod < min_lastmod:
                        continue
                except ValueError:
                    pass  # Can't parse lastmod — include the URL anyway
        urls.append(loc_text)
    return urls


def _extract_jsonld(html: str) -> dict[str, Any] | None:
    """Extract the first Event JSON-LD block from HTML."""
    tree = HTMLParser(html)
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text(deep=True)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            if data.get("@type") == "Event":
                return data
            # Drupal 8/9: {"@context": ..., "@graph": [...]}
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict) and item.get("@type") == "Event":
                        return item
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "Event":
                    return item
    return None


def _extract_drupal_settings(html: str) -> dict[str, Any] | None:
    """Extract Drupal settings JSON from a script tag.

    Drupal 8/9 sites embed settings as a raw JSON blob in a <script>
    tag (with data-drupal-selector="drupal-settings-json"), not via
    jQuery.extend. We look for any script whose content parses as JSON
    and contains map_markers or a path.currentPath node reference.
    """
    tree = HTMLParser(html)
    for script in tree.css("script"):
        text = script.text(deep=True)
        if not text or len(text) < 20:
            continue
        text = text.strip()
        if not text.startswith("{"):
            continue
        # Drupal embeds control characters (e.g. \x03 in pluralDelimiter)
        # that selectolax renders as raw bytes, breaking json.loads.
        sanitized = re.sub(r"[\x00-\x1f]", "", text)
        try:
            data = json.loads(sanitized)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if "path" in data or "map_markers" in data:
            return data
    return None


def _extract_age_range(text: str) -> tuple[int | None, int | None]:
    """Parse age range from body text. Returns (age_min, age_max)."""
    m = _AGE_RANGE_RX.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    if _AGE_ALL_RX.search(text):
        return 0, 99
    m = _AGE_PLUS_RX.search(text)
    if m:
        return int(m.group(1)), None
    return None, None


def _extract_price(jsonld: dict[str, Any] | None, body_text: str) -> Price:
    """Determine price from JSON-LD offers or body text."""
    if jsonld:
        offers = jsonld.get("offers")
        if isinstance(offers, dict):
            price_val = offers.get("price")
            if price_val is not None:
                try:
                    if float(price_val) == 0:
                        return Price.FREE
                    return Price.PAID
                except (ValueError, TypeError):
                    pass
        elif isinstance(offers, list):
            for offer in offers:
                if isinstance(offer, dict):
                    price_val = offer.get("price")
                    if price_val is not None:
                        try:
                            if float(price_val) == 0:
                                return Price.FREE
                            return Price.PAID
                        except (ValueError, TypeError):
                            continue

    lower = body_text.lower()
    if "free with admission" in lower or "free with museum admission" in lower:
        return Price.PAID
    if re.search(r"\bfree\b", lower):
        return Price.FREE
    if re.search(r"\$\d", lower):
        return Price.PAID
    return Price.UNKNOWN


def _infer_borough(
    lat: float | None,
    lng: float | None,
    venue: str | None,
) -> Borough | None:
    """Infer borough from coordinates or venue name."""
    if lat is not None and lng is not None:
        for borough, lat_min, lat_max, lng_min, lng_max in _BOROUGH_BOXES:
            if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
                return borough
    if venue:
        venue_lower = venue.lower()
        for name, borough in _VENUE_BOROUGH_LOOKUP.items():
            if name in venue_lower:
                return borough
    return None


def _infer_tags(title: str, description: str | None) -> list[str]:
    """Infer tags from title and description text."""
    haystack = title.lower()
    if description:
        haystack += " " + description.lower()
    tags: list[str] = []
    for tag, keywords in _KID_KEYWORDS:
        if any(kw in haystack for kw in keywords):
            tags.append(tag)
    return tags


def _find_map_markers(drupal: dict[str, Any]) -> list[dict] | None:
    """Find map_markers list in drupalSettings, which may be nested."""
    markers = drupal.get("map_markers")
    if isinstance(markers, list) and markers:
        return markers
    # Walk one level of nesting (Drupal views/field formatters)
    for v in drupal.values():
        if isinstance(v, dict):
            markers = v.get("map_markers")
            if isinstance(markers, list) and markers:
                return markers
    return None


def _extract_nid(
    drupal: dict[str, Any] | None,
    html: str,
) -> str | None:
    """Extract Drupal node ID from settings or googletag."""
    if drupal:
        # path.currentPath = "node/371180"
        current_path = drupal.get("path", {}).get("currentPath", "")
        if current_path.startswith("node/"):
            return current_path.removeprefix("node/")
        # map_markers[0].content.nid
        markers = _find_map_markers(drupal)
        if markers:
            content = markers[0].get("content")
            if isinstance(content, dict) and content.get("nid"):
                return str(content["nid"])
    # Fallback: googletag setTargeting('nid', '...')
    m = _NID_RX.search(html)
    if m:
        return m.group(1)
    return None


def _parse_detail_page(html_text: str, page_url: str) -> Event | None:
    """Parse an event detail page into an Event, or None if unparseable."""
    jsonld = _extract_jsonld(html_text)
    drupal = _extract_drupal_settings(html_text)

    # Title: prefer JSON-LD, fall back to drupalSettings
    title = None
    if jsonld:
        title = jsonld.get("name")
    if not title and drupal:
        # Drupal 8/9: try map_markers content, then path
        markers_for_title = _find_map_markers(drupal)
        if markers_for_title:
            content = markers_for_title[0].get("content")
            if isinstance(content, dict):
                title = content.get("node_title")
    if not title:
        return None

    # Start date is required — skip events without it
    start_dt = None
    if jsonld and jsonld.get("startDate"):
        try:
            start_dt = datetime.fromisoformat(jsonld["startDate"])
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    if start_dt is None:
        logger.debug("mommy_poppins: skipping %s — no parseable startDate", page_url)
        return None

    # End date (optional)
    end_dt = None
    if jsonld and jsonld.get("endDate"):
        try:
            end_dt = datetime.fromisoformat(jsonld["endDate"])
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=UTC)
        except ValueError:
            pass

    # Description
    description = jsonld.get("description") if jsonld else None

    # URL
    url = None
    if jsonld:
        url = jsonld.get("url")
    if not url:
        url = page_url

    # Venue
    venue_name = None
    if jsonld:
        location = jsonld.get("location")
        if isinstance(location, dict):
            venue_name = location.get("name")
            if isinstance(venue_name, list):
                venue_name = ", ".join(str(v) for v in venue_name)

    # Extract map_markers from drupalSettings (may be top-level or nested)
    markers = _find_map_markers(drupal) if drupal else None

    if not venue_name and markers:
        # Marker title is often "m0"; real venue is in content.title
        content = markers[0].get("content")
        if isinstance(content, dict) and content.get("title"):
            venue_name = content["title"]
        else:
            marker_title = markers[0].get("title", "")
            if marker_title and not re.match(r"^m\d+$", marker_title):
                venue_name = marker_title

    # Coordinates from drupalSettings map_markers
    lat = None
    lng = None
    if markers:
        coords = markers[0].get("coords")
        if isinstance(coords, dict):
            try:
                lat = float(
                    coords.get("latitude", coords.get("lat", 0)),
                )
                lng = float(
                    coords.get("longitude", coords.get("lng", 0)),
                )
                if lat == 0 and lng == 0:
                    lat = lng = None
            except (ValueError, TypeError):
                lat = lng = None

    # Borough
    borough = _infer_borough(lat, lng, venue_name)

    # Age range from body text
    tree = HTMLParser(html_text)
    body_text = tree.body.text() if tree.body else ""
    age_min, age_max = _extract_age_range(body_text)

    # Price
    price = _extract_price(jsonld, body_text)

    # External ID: prefer Drupal node ID, fall back to URL slug
    external_id = _extract_nid(drupal, html_text)
    if not external_id:
        slug = page_url.rstrip("/").rsplit("/", 1)[-1]
        if slug:
            external_id = slug

    # Tags
    tags = _infer_tags(title, description)

    # Raw payload: keep JSON-LD + lightweight drupal extract (not the
    # full drupalSettings blob, which is huge)
    raw: dict[str, Any] = {"page_url": page_url}
    if jsonld:
        raw["jsonld"] = jsonld
    if markers:
        raw["map_markers"] = markers
    if drupal and "path" in drupal:
        raw["drupal_path"] = drupal["path"]

    return Event(
        id=compute_id(
            "mommy_poppins",
            external_id=external_id,
            url=url,
            title=title,
            venue=venue_name,
        ),
        source="mommy_poppins",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=venue_name,
        borough=borough,
        lat=lat,
        lng=lng,
        age_min=age_min,
        age_max=age_max,
        price=price,
        tags=tags,
        raw_payload=json.dumps(raw, default=str, sort_keys=True),
    )


class MommyPoppinsSource(Source):
    """Mommy Poppins NYC family events via sitemap discovery + page scraping."""

    name = "mommy_poppins"

    def __init__(
        self,
        *,
        sitemap_url: str = SITEMAP_INDEX_URL,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        request_delay: float = REQUEST_DELAY_SECONDS,
        http_timeout: float = 30.0,
    ):
        self._sitemap_url = sitemap_url
        self._lookback_days = lookback_days
        self._delay = request_delay
        self._timeout = http_timeout

    def fetch(self) -> Iterable[Event]:
        min_lastmod = datetime.now(UTC) - timedelta(days=self._lookback_days)
        event_urls = self._discover_event_urls(min_lastmod)
        logger.info("mommy_poppins: discovered %d event URLs", len(event_urls))

        for url in event_urls:
            try:
                html = self._fetch_page(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("mommy_poppins: failed to fetch %s: %r", url, exc)
                continue

            try:
                ev = _parse_detail_page(html, url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("mommy_poppins: failed to parse %s: %r", url, exc)
                continue

            if ev is not None:
                yield ev

            time.sleep(self._delay)

    def _discover_event_urls(self, min_lastmod: datetime) -> list[str]:
        """Fetch sitemap index and all sitemap pages, return NYC event URLs."""
        try:
            index_xml = self._fetch_page(self._sitemap_url)
        except Exception as exc:  # noqa: BLE001
            logger.error("mommy_poppins: failed to fetch sitemap index: %r", exc)
            return []

        sitemap_page_urls = _parse_sitemap_index(index_xml)
        if not sitemap_page_urls:
            # The index itself might be a urlset (single sitemap)
            return _parse_sitemap_page(index_xml, NYC_EVENT_URL_PREFIX, min_lastmod)

        all_event_urls: list[str] = []
        for sitemap_url in sitemap_page_urls:
            try:
                page_xml = self._fetch_page(sitemap_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mommy_poppins: failed to fetch sitemap page %s: %r",
                    sitemap_url, exc,
                )
                continue
            event_urls = _parse_sitemap_page(page_xml, NYC_EVENT_URL_PREFIX, min_lastmod)
            all_event_urls.extend(event_urls)
            time.sleep(self._delay)

        return all_event_urls

    def _fetch_page(self, url: str) -> str:
        """Fetch a URL and return the response text. Raises on HTTP errors."""
        resp = cffi_requests.get(
            url,
            timeout=self._timeout,
            impersonate="chrome",
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
