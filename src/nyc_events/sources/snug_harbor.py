"""Snug Harbor Cultural Center & Botanical Garden events.

Snug Harbor is an 83-acre cultural park in Livingston, Staten Island (a former
sailors' retirement campus) housing the Staten Island Children's Museum, the
Chinese Scholar's Garden, the Newhouse Center for Contemporary Art, Heritage
Farm, and performance/education spaces. It runs a mixed calendar — family
workshops, farm/garden programs, concerts, exhibitions, plus adult
fundraisers and residencies — so this is a **category-gated** source, not a
curated kids feed: we include only events tagged for a youth/family
`audience` taxonomy term and keep the shared adult blocklist as a safety net.

This is real Staten Island coverage — a borough the catalog is thin on (only
`si_childrens_museum`, which shares this campus).

Data flow:
  1. The site is WordPress. A custom `event` post type is exposed on the
     standard WP REST API (`/wp-json/wp/v2/event`) with rich taxonomies
     (`audience`, `cost-tier`, `genre`, `program`, `venue`) but **no event
     date in the REST payload** — `acf` is empty and the post `date` is the
     creation date, not the event date.
  2. The event **date lives only on the detail page**, in a JSON-LD `Event`
     node (`startDate`/`endDate`, ISO with a real -04:00/-05:00 offset). So,
     like `mommy_poppins`, we fetch the list cheaply then crawl each event's
     detail page for its JSON-LD.
  3. The REST list is **not date-sorted** (it's newest-created first) and
     accumulates past events, so we can't filter by date server-side — we
     fetch every youth/family event, read its JSON-LD date, and keep only
     those inside [today, today+window_days].

Quirks (verified live 2026-07-13):
  - **Transport is `curl_cffi` with `impersonate="chrome"`, NOT plain httpx.**
    The site sits behind Cloudflare bot management, which intermittently 403s
    a plain-httpx client's TLS fingerprint (a `403 Forbidden` on the taxonomy
    resolve was the first symptom — see the 2026-07-14 fix). Impersonating
    Chrome's TLS/HTTP2 fingerprint clears it, the same treatment the Tribe
    sources use. Do NOT revert to httpx.
  - **Kid filter is the `audience` taxonomy, resolved by NAME not id.** We
    resolve the audience terms once per run and query the `event` endpoint
    for the union of {Kids, Families, All Ages, Teens} term ids (an OR
    filter). Resolving by name survives a term-id renumber upstream. A
    "Teens"-only event (no Kids/Families/All Ages) is kept but does NOT get
    the "best for kids" tag (mirrors `new_york_family`).
  - **Every other taxonomy is resolved id->name once per run too**
    (`cost-tier` -> price, `genre`/`program` -> tags, `venue` -> sub-location),
    the same "resolve the taxonomy once, pass the maps to the pure parser"
    shape as `brooklyn_bridge_park`'s location resolution. Term names carry
    HTML entities (`$10 &amp; Under`) — unescaped before use.
  - **Price from `cost-tier`:** "Free" -> FREE; the priced tiers ($10/$25/$50
    & under, Above $50) -> PAID; "Pay What You Wish", "#N/A", or no tier ->
    UNKNOWN.
  - **Venue/borough hardcoded** "Snug Harbor Cultural Center & Botanical
    Garden" / Staten Island (single campus). The per-event `venue` taxonomy
    (Chinese Scholar's Garden, Great Hall, Heritage Farm, ...) is a spot
    within the campus — kept in `raw_payload`, not used as the venue name, so
    the `SOURCE_NEIGHBORHOOD["snug_harbor"] = "Snug Harbor"` coding applies to
    every row.
  - **`external_id` = the WP post id** (unique per event; the detail JSON-LD
    is one occurrence, no per-day expansion needed — recurring programs are
    filed as separate posts, same as `si_childrens_museum`).
  - **Full-window re-fetch → opted into missing-detection** (`window_days`):
    every run re-reads the whole youth/family event list, so an in-window
    future event that vanishes is a real removal.
  - JSON-LD `name` carries a " | Snug Harbor" suffix; we use the clean REST
    `title.rendered` instead.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any
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

BASE_URL = "https://snug-harbor.org"
REST_BASE = f"{BASE_URL}/wp-json/wp/v2"
VENUE_NAME = "Snug Harbor Cultural Center & Botanical Garden"
DEFAULT_WINDOW_DAYS = 60
REQUEST_DELAY_SECONDS = 0.5
PER_PAGE = 100
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# `audience` terms (by name) that make an event youth/family-relevant. Resolved
# to ids at fetch time for the REST query; also used by the parser to decide
# the "best for kids" tag. Teens is included in the fetch (teen programming is
# still youth programming) but does NOT on its own earn "best for kids".
KID_AUDIENCE_NAMES = ("kids", "families", "all ages", "teens")
_BEST_FOR_KIDS_AUDIENCES = ("kids", "families", "all ages")

# `cost-tier` term names that mean the event costs money. "Free" is free;
# everything else ("Pay What You Wish", "#N/A", unknown) stays UNKNOWN.
_PAID_COST_TIERS = ("$10", "$25", "$50", "above $")
_FREE_COST_TIER = "free"

# `genre`/`program` term name (normalized, substring) -> our tag vocabulary.
_GENRE_TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("arts and crafts", ("hands-on activity", "art-making", "craft", "art lab")),
    ("music", ("music", "performing arts", "concert")),
    ("theater", ("dance", "puppet", "theater")),
    ("movies", ("film", "screening")),
    ("outdoors", ("garden", "nature", "environment", "composting", "farm",
                  "urban growing", "stewardship", "tree care")),
    ("festival", ("festival", "community celebration")),
    ("educational", ("workshop", "tour", "history", "educational",
                     "environmental education", "informational", "cooking")),
    ("food", ("food and drink", "food & drink", "heritage farm stand")),
    ("art", ("exhibition", "architecture", "public art")),
]

_WS_RX = re.compile(r"\s+")


def _clean_text(raw: str | None) -> str:
    """Unescape entities, strip tags, collapse whitespace."""
    if not raw:
        return ""
    text = _html.unescape(raw)
    if "<" in text:
        text = HTMLParser(text).text(separator=" ", strip=True)
    return _WS_RX.sub(" ", text).strip()


def _term_names(ids: list[int] | None, term_map: dict[str, str]) -> list[str]:
    """Resolve a list of term ids to their (entity-unescaped) names."""
    out: list[str] = []
    for tid in ids or []:
        name = term_map.get(str(tid))
        if name:
            out.append(_html.unescape(name))
    return out


def _parse_jsonld_dt(raw: str | None) -> datetime | None:
    """Parse a JSON-LD ISO datetime into an NY-aware datetime.

    Snug Harbor stamps a correct -04:00/-05:00 offset, so unlike the
    Governors Island / Mommy Poppins "floating" timestamps we trust the
    offset and convert to America/New_York (keeps DST correct).
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=NYC_TZ)
    return dt.astimezone(NYC_TZ)


def extract_event_jsonld(html_text: str) -> dict[str, Any] | None:
    """Pull the first `@type == Event` node out of a detail page's JSON-LD.

    Snug Harbor emits a Yoast `@graph` array; the Event node carries
    startDate/endDate/description. Returns only the fields we use so the
    fixture stays small.
    """
    tree = HTMLParser(html_text)
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text(deep=True)
        if not text or '"Event"' not in text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if isinstance(node, dict) and node.get("@type") == "Event":
                return node
    return None


def _resolve_price(cost_tiers: list[str]) -> Price:
    """Map resolved cost-tier names to a Price."""
    lowered = [t.lower() for t in cost_tiers]
    if any(_FREE_COST_TIER == t for t in lowered):
        return Price.FREE
    if any(any(p in t for p in _PAID_COST_TIERS) for t in lowered):
        return Price.PAID
    return Price.UNKNOWN


def _infer_tags(audiences: list[str], genres: list[str], programs: list[str]) -> list[str]:
    """Build the tag list from audience + genre/program taxonomy names."""
    tags: list[str] = ["family"]
    aud_lower = [a.lower() for a in audiences]
    if any(a in _BEST_FOR_KIDS_AUDIENCES for a in aud_lower):
        tags.append("best for kids")
    haystack = " ".join(genres + programs).lower()
    for tag, keywords in _GENRE_TAGS:
        if tag not in tags and any(kw in haystack for kw in keywords):
            tags.append(tag)
    return tags


def _is_kid_relevant(title: str, description: str | None, audiences: list[str]) -> bool:
    """Safety net on top of the audience-taxonomy gate: drop the occasional
    adult fundraiser/21+ night that still carries a family audience tag."""
    haystack = f"{title} {description or ''}"
    if contains_any(haystack, ADULT_BLOCKLIST):
        return False
    if contains_any(title, ADULT_TITLE_BLOCKLIST) or contains_any(title, MEMBERS_ONLY):
        return False
    return True


def parse_event(
    item: dict[str, Any],
    jsonld: dict[str, Any] | None,
    terms: dict[str, dict[str, str]],
    today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Event | None:
    """Parse one REST list item + its detail JSON-LD into an Event, or None.

    Pure function (no network) — the fixture test exercises it directly with
    captured `item`/`jsonld` dicts and the resolved `terms` maps. Returns None
    when filtered out, undated, or outside [today, today+window_days].
    """
    title = _clean_text((item.get("title") or {}).get("rendered"))
    if not title:
        return None

    if jsonld is None:
        logger.debug("snug_harbor: skipping %r — no Event JSON-LD", title)
        return None

    start_dt = _parse_jsonld_dt(jsonld.get("startDate"))
    if start_dt is None:
        logger.debug("snug_harbor: skipping %r — no parseable startDate", title)
        return None

    # Window filter (the REST list is undated + accumulates past events).
    start_date = start_dt.date()
    if not (today <= start_date <= today + timedelta(days=window_days)):
        return None

    audiences = _term_names(item.get("audience"), terms.get("audience", {}))
    description = _clean_text(jsonld.get("description")) or None

    if not _is_kid_relevant(title, description, audiences):
        return None

    end_dt = _parse_jsonld_dt(jsonld.get("endDate"))
    cost_tiers = _term_names(item.get("cost-tier"), terms.get("cost-tier", {}))
    genres = _term_names(item.get("genre"), terms.get("genre", {}))
    programs = _term_names(item.get("program"), terms.get("program", {}))
    sub_venues = _term_names(item.get("venue"), terms.get("venue", {}))

    url = item.get("link") or jsonld.get("url")
    external_id = str(item["id"]) if item.get("id") is not None else None
    price = _resolve_price(cost_tiers)
    tags = _infer_tags(audiences, genres, programs)

    raw = {
        "id": item.get("id"),
        "link": item.get("link"),
        "audience": audiences,
        "cost_tier": cost_tiers,
        "genre": genres,
        "program": programs,
        "sub_venue": sub_venues,
        "jsonld": jsonld,
    }

    return Event(
        id=compute_id(
            "snug_harbor",
            external_id=external_id,
            url=url,
            title=title,
            venue=VENUE_NAME,
        ),
        source="snug_harbor",
        external_id=external_id,
        title=title,
        description=description,
        url=url,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=VENUE_NAME,
        borough=Borough.STATEN_ISLAND,
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=price,
        tags=tags,
        raw_payload=json.dumps(raw, default=str, sort_keys=True),
    )


class SnugHarborSource(Source):
    """Snug Harbor events: WP REST `event` list + per-detail JSON-LD dates."""

    name = "snug_harbor"
    display_name = "Snug Harbor Cultural Center & Botanical Garden"

    def __init__(
        self,
        *,
        rest_base: str = REST_BASE,
        window_days: int = DEFAULT_WINDOW_DAYS,
        request_delay: float = REQUEST_DELAY_SECONDS,
        http_timeout: float = 30.0,
    ):
        self._rest_base = rest_base
        self._window_days = window_days
        # Full youth/family list re-fetched every run → missing-detection eligible.
        self.window_days = window_days
        self._delay = request_delay
        self._timeout = http_timeout

    def fetch(self) -> Iterable[Event]:
        """Resolve taxonomies, list youth/family events, crawl each for its date."""
        with cffi_requests.Session(
            timeout=self._timeout,
            impersonate="chrome",
            headers={"User-Agent": USER_AGENT},
        ) as client:
            terms = self._resolve_terms(client)
            if terms is None:
                return  # hard failure already logged; ingest skips this source

            kid_ids = [
                tid
                for tid, name in terms.get("audience", {}).items()
                if _html.unescape(name).lower() in KID_AUDIENCE_NAMES
            ]
            if not kid_ids:
                logger.warning(
                    "snug_harbor: no youth/family audience terms resolved — skipping"
                )
                return

            items = self._list_events(client, kid_ids)
            logger.info("snug_harbor: %d youth/family events to check", len(items))

            today = datetime.now(NYC_TZ).date()
            total = 0
            for item in items:
                link = item.get("link")
                if not link:
                    continue
                try:
                    resp = client.get(link)
                    resp.raise_for_status()
                    html_text = resp.text
                except Exception:  # noqa: BLE001
                    logger.warning("snug_harbor: failed to fetch %s", link, exc_info=True)
                    continue
                jsonld = extract_event_jsonld(html_text)
                try:
                    ev = parse_event(item, jsonld, terms, today, self._window_days)
                except Exception:  # noqa: BLE001
                    logger.warning("snug_harbor: failed to parse %s", link, exc_info=True)
                    ev = None
                if ev is not None:
                    total += 1
                    yield ev
                time.sleep(self._delay)

        logger.info("snug_harbor: yielded %d events", total)

    def _resolve_terms(self, client: cffi_requests.Session) -> dict[str, dict[str, str]] | None:
        """Fetch each taxonomy's terms once → {taxonomy: {id: name}} maps."""
        terms: dict[str, dict[str, str]] = {}
        for tax in ("audience", "cost-tier", "genre", "program", "venue"):
            try:
                resp = client.get(f"{self._rest_base}/{tax}?per_page=100&_fields=id,name")
                resp.raise_for_status()
                data = resp.json()
            except Exception:  # noqa: BLE001
                logger.warning("snug_harbor: failed to resolve %s terms", tax, exc_info=True)
                return None
            terms[tax] = {str(t["id"]): t.get("name", "") for t in data}
        return terms

    def _list_events(
        self, client: cffi_requests.Session, kid_audience_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Page through the youth/family `event` list (metadata only)."""
        fields = "id,link,title,audience,cost-tier,genre,program,venue"
        aud = ",".join(kid_audience_ids)
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            url = (
                f"{self._rest_base}/event?audience={aud}"
                f"&per_page={PER_PAGE}&page={page}&_fields={fields}"
            )
            try:
                resp = client.get(url)
                if resp.status_code == 400:  # WP: invalid page = past the end
                    break
                resp.raise_for_status()
                data = resp.json()
            except Exception:  # noqa: BLE001
                logger.warning("snug_harbor: failed to list events page %d", page, exc_info=True)
                break
            if not data:
                break
            items.extend(data)
            if len(data) < PER_PAGE:
                break
            page += 1
            time.sleep(self._delay)
        return items
