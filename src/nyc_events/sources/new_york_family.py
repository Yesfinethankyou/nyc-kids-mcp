"""New York Family events (events.newyorkfamily.com) — Schneps network calendar.

New York Family is Schneps Media's parenting magazine; its events site is one
skin over a network-wide event pool (row meta names `events.amny.com` as the
hub). The pool is regional — Long Island and East End venues appear alongside
the five boroughs — and every syndicated row is family-tagged upstream, so
this source needs a GEOGRAPHY filter, not a kid-relevance filter.

The upstream is WordPress + The Events Calendar (Tribe), but do NOT reach for
`_tribe.TribeEventsSource` — the network has deliberately hobbled the REST API
(verified live 2026-07-12, and it changed shape in the six days since the
previous probe, so expect it to change again):

  - Every query returns at most 16 rows; ``per_page``/``page`` are ignored.
    ``page>1`` returns the SAME rows serialized as empty husks
    (``{"start_date", "end_date"}`` only) — never fetch page>1, and skip any
    row missing ``id``/``title`` defensively.
  - The response envelope is ``{"events": [...]}`` only — no ``total`` /
    ``next_rest_url``, so Tribe-style pagination cannot work.
  - ``start_date``/``end_date`` (and ``categories``) ARE honored.
    ``start_date`` has "ongoing at" semantics: all-day/multi-day rows return
    for every instant they span and sort first (ascending by start), so a
    within-day cursor advances past them only slowly.
  - Rows carry NO ``utc_start_date``/``utc_end_date`` — only a local
    ``start_date`` plus a ``timezone`` field (`America/New_York`), so
    ``_tribe.parse_row`` (which keys on the UTC fields) would drop every row.
    Only ``strip_html``/``parse_cost`` are reused from ``_tribe``.

Fetch strategy: walk the window one day at a time
(``start_date=<day> 00:00:00&end_date=<day> 23:59:59``). While a slice comes
back full (16 rows — the cap, meaning the day probably has more), re-query
with ``start_date`` advanced to the latest start seen (or +2h if stuck) and
union the results, deduplicating on ``(id, start_date)``. Quiet days cost one
request; a packed Saturday (69 real events counted on 2026-07-18) costs ~6-9.
Known residual loss: instants where >16 events are simultaneously ongoing.
Every run re-walks the full window → opted INTO missing-detection.

Quirks (verified live 2026-07-12):
  - Recurring events are expanded per-occurrence by the server per queried
    day, but occurrences SHARE the parent's numeric ``id`` ("The Very Hungry
    Caterpillar Show" id 853667 runs 11:30 AND 15:30 the same day) — so
    ``external_id = f"{id}:{start.isoformat()}"`` (the permit-source pattern),
    unlike the four Tribe venue sources where ids are per-occurrence.
  - ``venue.geo_lat``/``geo_lng`` were present on 100% of sampled rows —
    borough (and NYC membership) comes from the mommy_poppins coordinate
    boxes; a city-string map is the fallback. City strings alone are a trap
    ("New York City", "Manhatten" (sic), "Woodhaven" all appear). Rows that
    resolve to no borough are DROPPED as out-of-area (Long Island/East End
    noise), per the aggressive-filtering philosophy.
  - Category names carry structured age bands — ``Baby & Toddler (0–2)``,
    ``Preschoolers (3–4)``, ``Kids (5–8)``, ``Tweens (9–12)``,
    ``Teens (13–18)`` — parsed into ``age_min``/``age_max`` (min-of-mins /
    max-of-maxes). The first source with structured ages. Upstream sometimes
    sprays every band on one row (an 0–18 range) — harmless.
  - Categories are plain strings on this install (standard Tribe returns
    objects) and are HTML-escaped ("Craft &amp; DIY").
  - ``cost`` is free text ("Free", "$15", "Tickets start at $27", prose) →
    ``_tribe.parse_cost``; a "Free" category backstops an empty cost field.
  - The upstream `Nightlife` category is NOT a hard-exclude here: it showed up
    co-tagged with every age band on a Long Island family concert (their
    category tagging is spray-everything), and the geo filter already drops
    the observed cases. The shared adult blocklists are the safety net.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..models import Borough, Event, Price, compute_id
from ._filters import ADULT_BLOCKLIST, ADULT_TITLE_BLOCKLIST, MEMBERS_ONLY, contains_any
from ._tribe import parse_cost, strip_html
from .base import Source

logger = logging.getLogger(__name__)

EVENTS_URL = "https://events.newyorkfamily.com/wp-json/tribe/events/v1/events"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)
DEFAULT_WINDOW_DAYS = 35
REQUEST_DELAY_SECONDS = 0.75
DEFAULT_HTTP_TIMEOUT = 30.0
# The server's hard per-query row cap (route doc: per_page default "16").
# A slice returning exactly this many rows means the day may hold more.
_SERVER_ROW_CAP = 16
# Bound the within-day slice walk; the worst observed day (69 events) needs ~8.
MAX_SLICES_PER_DAY = 12
# When a slice's rows don't advance the cursor (16 ongoing/identical starts),
# jump ahead by this much instead. Events starting inside the skipped gap are
# the accepted residual loss.
_STUCK_ADVANCE = timedelta(hours=2)

_LOCAL_TZ = ZoneInfo("America/New_York")

# Age-band categories: "Kids (5–8)" → (5, 8). Upstream uses an en dash but
# don't count on it. Applied to every category name; non-band categories
# simply don't match.
_AGE_BAND_RX = re.compile(r"\((\d+)\s*[–—-]\s*(\d+)\)")

# Borough coordinate bounding boxes — copied from mommy_poppins.py (the
# established source of truth for this pattern; intentionally generous, first
# match wins). Doubles as the five-borough membership test: no box and no
# city-map hit → the row is Long Island/Westchester/NJ noise and is dropped.
_BOROUGH_BOXES: list[tuple[Borough, float, float, float, float]] = [
    (Borough.MANHATTAN, 40.700, 40.882, -74.020, -73.907),
    (Borough.BROOKLYN, 40.570, 40.739, -74.042, -73.855),
    (Borough.QUEENS, 40.541, 40.812, -73.962, -73.700),
    (Borough.BRONX, 40.785, 40.917, -73.934, -73.748),
    (Borough.STATEN_ISLAND, 40.496, 40.651, -74.255, -74.052),
]

# Fallback when a row has no usable coordinates: venue.city → borough.
# Queens venues routinely list their neighborhood as the city.
_CITY_BOROUGH: dict[str, Borough] = {
    "new york": Borough.MANHATTAN,
    "new york city": Borough.MANHATTAN,
    "manhattan": Borough.MANHATTAN,
    "manhatten": Borough.MANHATTAN,  # sic — appears upstream
    "ny": Borough.MANHATTAN,
    "harlem": Borough.MANHATTAN,
    "brooklyn": Borough.BROOKLYN,
    "bronx": Borough.BRONX,
    "the bronx": Borough.BRONX,
    "staten island": Borough.STATEN_ISLAND,
    "queens": Borough.QUEENS,
    "astoria": Borough.QUEENS,
    "long island city": Borough.QUEENS,
    "flushing": Borough.QUEENS,
    "corona": Borough.QUEENS,
    "jamaica": Borough.QUEENS,
    "forest hills": Borough.QUEENS,
    "woodhaven": Borough.QUEENS,
    "ridgewood": Borough.QUEENS,
    "springfield gardens": Borough.QUEENS,
    "jackson heights": Borough.QUEENS,
    "elmhurst": Borough.QUEENS,
    "bayside": Borough.QUEENS,
    "far rockaway": Borough.QUEENS,
    "rockaway park": Borough.QUEENS,
    "richmond hill": Borough.QUEENS,
    "ozone park": Borough.QUEENS,
    "queens village": Borough.QUEENS,
}

# Upstream category name (unescaped) → our tag vocabulary. Names not listed
# (Attractions, Community, Parents, Fundraisers, the age bands, "Family",
# "Free", …) intentionally map to nothing.
_CATEGORY_TAGS: dict[str, str] = {
    "art": "art",
    "art shows": "art",
    "craft & diy": "arts & crafts",
    "classes": "educational",
    "books & readings": "storytelling",
    "comedy": "theater",
    "theater": "theater",
    "dance": "dance",
    "music": "music",
    "concerts": "music",
    "movies": "movie",
    "movie screenings": "movie",
    "sports": "sports",
    "health & fitness": "fitness",
    "outdoors": "outdoors",
    "environment": "nature",
    "animals": "nature",
    "animals & pets": "nature",
    "boating": "waterfront",
    "festivals": "festival",
    "food & drink": "food",
    "games": "games",
    "history & culture": "history",
    "markets": "market",
    "tours": "tour",
    "volunteering": "volunteer",
}

# Age-band categories whose presence marks a row as kid-targeted (vs. merely
# family-welcome); "Teens (13–18)" alone doesn't earn "best for kids".
_KID_BAND_PREFIXES: tuple[str, ...] = ("baby & toddler", "preschoolers", "kids (", "tweens")


def _category_names(row: dict[str, Any]) -> list[str]:
    """This install's categories are plain (HTML-escaped) strings, not the
    standard Tribe objects — accept both shapes."""
    names = []
    for c in row.get("categories") or []:
        name = c if isinstance(c, str) else (c.get("name") or "")
        if name:
            names.append(html.unescape(name).strip())
    return names


def _parse_local_dt(raw: str | None, tz_name: str | None) -> datetime | None:
    """Parse a local 'YYYY-MM-DD HH:MM:SS' string into UTC-aware."""
    if not raw:
        return None
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    try:
        tz = ZoneInfo(tz_name) if tz_name else _LOCAL_TZ
    except (KeyError, ValueError):
        tz = _LOCAL_TZ
    return naive.replace(tzinfo=tz).astimezone(UTC)


def _to_float(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _resolve_borough(venue: dict[str, Any]) -> Borough | None:
    """Coordinate boxes first, city-string map second; None = not NYC."""
    lat = _to_float(venue.get("geo_lat"))
    lng = _to_float(venue.get("geo_lng"))
    if lat is not None and lng is not None:
        for borough, lat_min, lat_max, lng_min, lng_max in _BOROUGH_BOXES:
            if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
                return borough
    city = (venue.get("city") or "").strip().lower()
    return _CITY_BOROUGH.get(city)


def _parse_age_bands(categories: list[str]) -> tuple[int | None, int | None]:
    """Min-of-mins / max-of-maxes across all '(N–M)' category names."""
    lows: list[int] = []
    highs: list[int] = []
    for name in categories:
        m = _AGE_BAND_RX.search(name)
        if m:
            lows.append(int(m.group(1)))
            highs.append(int(m.group(2)))
    if not lows:
        return None, None
    return min(lows), max(highs)


def _infer_tags(categories: list[str]) -> list[str]:
    """'family' is unconditional (the site is a family calendar); age bands
    below teen add 'best for kids'; topical categories map via the table."""
    tags = ["family"]
    lowered = [c.lower() for c in categories]
    if any(c.startswith(_KID_BAND_PREFIXES) for c in lowered):
        tags.append("best for kids")
    for c in lowered:
        tag = _CATEGORY_TAGS.get(c)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _parse_row(row: dict[str, Any]) -> Event | None:
    """Parse one event row into an Event, or None if skipped.

    Skips: page>1 husk rows (no id/title), rows outside the five boroughs,
    rows with no parseable start, and adult-blocklist hits (safety net —
    everything here is nominally family programming).
    """
    if not row.get("id") or not row.get("title"):
        # The husk shape any page>1 fetch gets; harmless to skip silently.
        return None

    title = strip_html(row.get("title"))
    if not title:
        return None

    start_dt = _parse_local_dt(row.get("start_date"), row.get("timezone"))
    if start_dt is None:
        logger.debug("new_york_family: skipping %r — no parseable start date", title)
        return None

    venue = row.get("venue") or {}
    if not isinstance(venue, dict):
        venue = {}
    borough = _resolve_borough(venue)
    if borough is None:
        logger.debug("new_york_family: skipping %r — outside the five boroughs", title)
        return None

    description_text = strip_html(row.get("description"))
    excerpt_text = strip_html(row.get("excerpt"))
    description = excerpt_text or description_text or None
    if description and len(description) > 2000:
        description = description[:2000].rsplit(" ", 1)[0] + "…"

    haystack = f"{title} {description_text}"
    if (
        contains_any(haystack, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
        or contains_any(title, MEMBERS_ONLY)
    ):
        logger.debug("new_york_family: adult/members blocklist hit, skipping %r", title)
        return None

    categories = _category_names(row)
    age_min, age_max = _parse_age_bands(categories)

    cost = (row.get("cost") or "").strip()
    price = parse_cost(cost)
    if price is Price.UNKNOWN and any(c.lower() == "free" for c in categories):
        price = Price.FREE

    # Occurrences share the parent's numeric id (verified live — see module
    # docstring), so the id alone would collapse a recurring series into one
    # row; suffix the start to make each occurrence its own row.
    external_id = f"{row['id']}:{start_dt.isoformat()}"

    return Event(
        id=compute_id("new_york_family", external_id=external_id),
        source="new_york_family",
        external_id=external_id,
        title=title,
        description=description,
        url=row.get("url") or None,
        start_dt=start_dt,
        end_dt=_parse_local_dt(row.get("end_date"), row.get("timezone")),
        venue_name=(venue.get("venue") or "").strip() or None,
        borough=borough,
        neighborhood=None,  # enrich pass codes it (lat/lng arrives with the row)
        lat=_to_float(venue.get("geo_lat")),
        lng=_to_float(venue.get("geo_lng")),
        age_min=age_min,
        age_max=age_max,
        price=price,
        tags=_infer_tags(categories),
        raw_payload=json.dumps(row, sort_keys=True, default=str),
    )


def _next_slice_start(rows: list[dict[str, Any]], current: datetime) -> datetime:
    """Advance the within-day cursor past what this slice showed.

    ``rows`` is a full slice (the server cap), sorted by start ascending —
    the max parseable local start is the frontier. If that doesn't move us
    forward (every visible row is ongoing / started at the cursor), jump
    ``_STUCK_ADVANCE`` so the walk always terminates.
    """
    latest = current
    for row in rows:
        raw = row.get("start_date")
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S") if raw else None
        except (ValueError, TypeError):
            dt = None
        if dt is not None and dt > latest:
            latest = dt
    if latest > current:
        return latest
    return current + _STUCK_ADVANCE


class NewYorkFamilySource(Source):
    """New York Family events via the day-walk over the capped Tribe API."""

    name = "new_york_family"
    display_name = "New York Family"

    def __init__(
        self,
        *,
        events_url: str = EVENTS_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        request_delay: float = REQUEST_DELAY_SECONDS,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
        max_slices_per_day: int = MAX_SLICES_PER_DAY,
    ):
        self._events_url = events_url
        # Every run re-walks the full window day by day → missing-detection
        # eligible (an in-window future event absent from the union was
        # removed upstream, modulo the documented >16-simultaneous residual).
        self.window_days = window_days
        self._delay = request_delay
        self._timeout = http_timeout
        self._max_slices = max_slices_per_day

    _parse_row = staticmethod(_parse_row)

    def fetch(self) -> Iterable[Event]:
        """Day-walk the window, slicing busy days, deduping on (id, start)."""
        today = datetime.now(_LOCAL_TZ).date()
        seen: set[tuple[int, str]] = set()
        total = 0

        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            for offset in range(self.window_days):
                day = today + timedelta(days=offset)
                yielded = yield from self._walk_day(client, day, seen)
                if yielded is None:
                    # Hard HTTP failure mid-run — stop rather than leave a
                    # silent hole in the middle of the window (the ingest
                    # circuit breaker judges the partial yield).
                    logger.warning(
                        "new_york_family: aborting run at %s after fetch failure", day
                    )
                    break
                total += yielded

        logger.info("new_york_family: yielded %d events", total)

    def _walk_day(
        self,
        client: httpx.Client,
        day: date,
        seen: set[tuple[int, str]],
    ) -> Iterable[Event]:
        """Slice one day until a partial page; returns rows yielded, or None
        on a hard fetch failure (generator return value)."""
        cursor = datetime.combine(day, dtime.min)
        day_end = f"{day} 23:59:59"
        yielded = 0

        for _ in range(self._max_slices):
            rows = self._get_slice(client, cursor.strftime("%Y-%m-%d %H:%M:%S"), day_end)
            if rows is None:
                return None

            full_rows = [r for r in rows if r.get("id") and r.get("title")]
            for row in full_rows:
                key = (row["id"], row.get("start_date") or "")
                if key in seen:
                    continue
                seen.add(key)
                try:
                    ev = self._parse_row(row)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "new_york_family: failed to parse event id=%r",
                        row.get("id"),
                        exc_info=True,
                    )
                    continue
                if ev is not None:
                    yielded += 1
                    yield ev

            if len(rows) < _SERVER_ROW_CAP:
                break  # the day fit under the cap — done
            cursor = _next_slice_start(full_rows, cursor)
            if cursor.date() != day:
                break
            time.sleep(self._delay)

        time.sleep(self._delay)
        return yielded

    def _get_slice(
        self,
        client: httpx.Client,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]] | None:
        """One capped query. Returns rows (husks included — the caller
        filters) or None on HTTP error."""
        try:
            resp = client.get(
                self._events_url,
                params={"start_date": start_date, "end_date": end_date},
            )
            resp.raise_for_status()
            return list(resp.json().get("events") or [])
        except Exception:  # noqa: BLE001
            logger.warning(
                "new_york_family: failed to fetch slice %s → %s",
                start_date,
                end_date,
                exc_info=True,
            )
            return None
