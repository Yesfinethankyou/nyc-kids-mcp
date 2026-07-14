"""Brooklyn Bridge Park events.

Brooklyn Bridge Park (the waterfront park conservancy, brooklynbridgepark.org)
runs a large free public program calendar: kayaking, storytime with BPL,
youth sports clinics, nature education ("Expert-Led Explorations"),
stargazing, waterfront movies, plus adult programming (fitness classes,
dance parties, galas) that we filter. WordPress, but NOT Tribe — the custom
`events` post type is exposed on the standard WP REST API with ACF fields:

    GET /wp-json/wp/v2/events?per_page=100&page=N

Row shape (verified live, 2026-07-13; 671 posts, ~224 future occurrences):
  - `acf.date` = local calendar date as YYYYMMDD; `acf.start_time` /
    `acf.end_time` = "H:MM am/pm" wall times (America/New_York). No UTC
    fields. `multi-day` is False on every live row.
  - Recurring programs are posted BOTH ways, and the two overlap: a
    recurring PARENT post (`recurring_event: true` + a
    `select_date_&_time` array of additional occurrences) AND individual
    dated posts titled "<Program> – July 14". Expanding parents and also
    keeping dated posts double-counts, so occurrences are DEDUPED on
    (base title, date) — the dated-suffix is stripped for the key — and
    the dated post wins (its URL/description are occurrence-specific).
  - `external_id = f"{post_id}:{date}"` — parents expand to many dates.
  - `event_category` is a real taxonomy (term ids are stable; snapshot in
    `_CATEGORIES`). There is NO kids category, so kid-relevance is
    inclusive-with-blocklist (see below).
  - `acf.event_location` references the `maplocations` post type (pier /
    lawn / playground names); fetch() resolves the id→name map once per
    run (one extra request) for venue names, falling back to the park name.

Kid-relevance strategy (inclusive + blocklist — the Prospect Park posture,
adapted because there's no kids category):
  - Shared adult blocklists on the TITLE ONLY, plus local extras
    ("healthy aging" — the seniors program). Body text is deliberately not
    checked: BBP descriptions carry registration fine print ("a
    parent/guardian who is 18+ must register" — on Pokémon Day Out!), so a
    body-scope "18+" match would drop squarely kid-relevant events.
  - Category hard-excludes: Benefit Events (galas), Socials & Dancing
    (adult dance parties), Volunteer (orientations/cleanups — not kid
    programming).
  - Fitness is excluded UNLESS the title carries a family signal
    (family/kids/youth/toddler/stroller/teen): plain "Sunset Yoga" and
    "Amp'd Bootcamp" are adult classes, but "Family Kayaking" and "Youth
    Basketball Clinics" are keepers.
  - Everything else passes: Arts & Culture, Environmental Education,
    Live Music, Movies, Tours, Public Art, and uncategorized rows
    (Storytime with BPL is uncategorized — a category-required gate would
    lose it).

Price: kept rows are the park's free public programming → Price.FREE
(same posture as Brooklyn Army Terminal's kept community events).

Full-window semantics: every run re-fetches the entire post collection
(7 pages at per_page=100) and yields occurrences inside [today, today+60d]
→ full re-fetch → opts INTO missing-event detection.
"""

from __future__ import annotations

import json
import logging
import re
import time as time_mod
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..models import Borough, Event, Price, compute_id
from ._filters import (
    ADULT_BLOCKLIST,
    ADULT_TITLE_BLOCKLIST,
    MEMBERS_ONLY,
    contains_any,
)
from ._tribe import strip_html  # canonical HTML→prose helper (not Tribe-specific)
from .base import Source

NYC_TZ = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

BASE_URL = "https://brooklynbridgepark.org"
EVENTS_URL = f"{BASE_URL}/wp-json/wp/v2/events"
LOCATIONS_URL = f"{BASE_URL}/wp-json/wp/v2/maplocations"
VENUE_NAME = "Brooklyn Bridge Park"
DEFAULT_WINDOW_DAYS = 60
DEFAULT_PER_PAGE = 100
PAGE_DELAY_SECONDS = 0.75
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)

# event_category term-id snapshot (verified live 2026-07-13). Unknown ids are
# simply ignored — the filter and tags degrade gracefully if terms are added.
_CATEGORIES: dict[int, str] = {
    342: "Benefit Events",
    343: "Arts & Culture",
    344: "Public Art",
    345: "Socials & Dancing",
    346: "Live Music",
    347: "Environmental Education",
    348: "Fitness",
    349: "Tours",
    350: "Volunteer",
    353: "Movies",
}

_EXCLUDED_CATEGORIES = frozenset({"Benefit Events", "Socials & Dancing", "Volunteer"})

# Local adult-programming signals beyond the shared blocklists.
_LOCAL_BLOCKLIST: tuple[str, ...] = ("healthy aging",)

# Family signal that rescues a Fitness-category row (and adds the
# "best for kids" tag anywhere it appears).
_FAMILY_TITLE_RX = re.compile(r"\b(family|families|kids?|youth|toddlers?|strollers?|teens?)\b")

# Dated recurring posts are titled "<Program> – July 14" (en/em dash or
# hyphen). Stripping the suffix yields the dedup key shared with the parent.
_DATED_SUFFIX_RX = re.compile(
    r"\s*[–—-]\s*(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{1,2}$",
    re.IGNORECASE,
)

_TIME_RX = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", re.IGNORECASE)

_CATEGORY_TAGS: dict[str, str] = {
    "Arts & Culture": "cultural",
    "Public Art": "cultural",
    "Environmental Education": "nature",
    "Live Music": "music",
    "Movies": "movie",
    "Tours": "educational",
    "Fitness": "sports",
}

_TITLE_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("story time", ("storytime", "story time")),
    ("nature", ("nature", "bird", "fishing", "stargazing", "oyster", "plankton", "insect")),
    ("sports", ("kayak", "basketball", "soccer", "sports")),
    ("movie", ("movie", "film")),
]


def _acf(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("acf") or {}


def _title(row: dict[str, Any]) -> str:
    return strip_html((row.get("title") or {}).get("rendered"))


def _base_title(title: str) -> str:
    """Title with any trailing '– July 14'-style dated suffix removed."""
    return _DATED_SUFFIX_RX.sub("", title).strip()


def _category_names(row: dict[str, Any]) -> set[str]:
    return {_CATEGORIES[c] for c in row.get("event_category") or [] if c in _CATEGORIES}


def _parse_wall_time(raw: str | None) -> tuple[int, int] | None:
    m = _TIME_RX.search(raw or "")
    if not m:
        return None
    hour, minute, mer = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if mer == "p" and hour != 12:
        hour += 12
    elif mer == "a" and hour == 12:
        hour = 0
    return (hour, minute) if hour < 24 else None


def _parse_acf_date(raw: str | None) -> date | None:
    """Parse the ACF YYYYMMDD local calendar date."""
    if not raw or not re.fullmatch(r"\d{8}", str(raw)):
        return None
    s = str(raw)
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _occurrences(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The post's occurrence list: its own ACF date/time plus, for recurring
    parents, each entry of the `select_date_&_time` array. Occurrence
    entries missing times inherit the parent's."""
    acf = _acf(row)
    base = {
        "date": acf.get("date"),
        "start_time": acf.get("start_time"),
        "end_time": acf.get("end_time"),
    }
    occs = [base]
    for extra in acf.get("select_date_&_time") or []:
        occs.append(
            {
                "date": extra.get("date"),
                "start_time": extra.get("start_time") or base["start_time"],
                "end_time": extra.get("end_time") or base["end_time"],
            }
        )
    return occs


def _is_kid_relevant(row: dict[str, Any]) -> bool:
    # Blocklists check the TITLE ONLY here (the per-source scope decision
    # _filters.py calls out): BBP descriptions are long-form and routinely
    # mention registration fine print — "a parent/guardian who is 18+ must
    # register" appears in the body of Pokémon Day Out, the most
    # kid-relevant event on the calendar.
    title = _title(row)
    haystack_title = title.lower()
    if (
        contains_any(title, ADULT_BLOCKLIST)
        or contains_any(title, ADULT_TITLE_BLOCKLIST)
        or contains_any(title, MEMBERS_ONLY)
        or contains_any(title, _LOCAL_BLOCKLIST)
    ):
        return False
    categories = _category_names(row)
    if categories & _EXCLUDED_CATEGORIES:
        return False
    if "Fitness" in categories and not _FAMILY_TITLE_RX.search(haystack_title):
        return False
    return True


def _infer_tags(title: str, categories: set[str]) -> list[str]:
    tags: list[str] = ["family"]
    if _FAMILY_TITLE_RX.search(title.lower()):
        tags.append("best for kids")
    for cat, tag in _CATEGORY_TAGS.items():
        if cat in categories and tag not in tags:
            tags.append(tag)
    title_lower = title.lower()
    for tag, keywords in _TITLE_TAG_RULES:
        if tag not in tags and any(
            re.search(rf"\b{re.escape(kw)}", title_lower) for kw in keywords
        ):
            tags.append(tag)
    return tags


def _venue_name(row: dict[str, Any], locations: dict[int, str]) -> str:
    for loc_id in _acf(row).get("event_location") or []:
        name = locations.get(loc_id)
        if name:
            return f"{name}, {VENUE_NAME}"
    return VENUE_NAME


def _build_occurrence_event(
    row: dict[str, Any],
    occ: dict[str, Any],
    day: date,
    locations: dict[int, str],
) -> Event:
    title = _title(row)
    start_hm = _parse_wall_time(occ.get("start_time")) or (0, 0)
    end_hm = _parse_wall_time(occ.get("end_time"))
    start_dt = datetime(day.year, day.month, day.day, *start_hm, tzinfo=NYC_TZ)
    end_dt = None
    if end_hm is not None:
        end_dt = datetime(day.year, day.month, day.day, *end_hm, tzinfo=NYC_TZ)
        if end_dt <= start_dt:
            end_dt = None
    external_id = f"{row['id']}:{day.isoformat()}"
    categories = _category_names(row)
    _skip = ("yoast_head", "yoast_head_json", "_links", "content")
    raw = {k: v for k, v in row.items() if k not in _skip}
    return Event(
        id=compute_id("brooklyn_bridge_park", external_id=external_id),
        source="brooklyn_bridge_park",
        external_id=external_id,
        title=title,
        description=strip_html(_acf(row).get("description")) or None,
        url=row.get("link") or None,
        start_dt=start_dt,
        end_dt=end_dt,
        venue_name=_venue_name(row, locations),
        borough=Borough.BROOKLYN,
        lat=None,
        lng=None,
        age_min=None,
        age_max=None,
        price=Price.FREE,
        tags=_infer_tags(title, categories),
        raw_payload=json.dumps(raw, sort_keys=True, default=str),
    )


def parse_posts(
    rows: list[dict[str, Any]],
    *,
    locations: dict[int, str] | None = None,
) -> list[Event]:
    """Expand + filter + dedup a list of WP event posts (pure function).

    Dedup: recurring parents and their per-date dated posts describe the same
    real-world occurrences; key on (base title lowercased, date) and prefer
    the dated (non-recurring) post.
    """
    locations = locations or {}
    chosen: dict[tuple[str, date], tuple[bool, dict[str, Any], dict[str, Any]]] = {}
    for row in rows:
        try:
            if not _is_kid_relevant(row):
                continue
            is_recurring = bool(_acf(row).get("recurring_event"))
            key_title = _base_title(_title(row)).lower()
            for occ in _occurrences(row):
                day = _parse_acf_date(occ.get("date"))
                if day is None:
                    continue
                key = (key_title, day)
                existing = chosen.get(key)
                # Dated posts (non-recurring) win over parent expansions.
                if existing is None or (existing[0] and not is_recurring):
                    chosen[key] = (is_recurring, row, occ)
        except Exception:  # noqa: BLE001
            logger.warning(
                "brooklyn_bridge_park: failed to parse post id=%r",
                row.get("id"),
                exc_info=True,
            )
            continue

    events = []
    for (_, day), (_, row, occ) in sorted(chosen.items(), key=lambda kv: kv[0][1]):
        try:
            events.append(_build_occurrence_event(row, occ, day, locations))
        except Exception:  # noqa: BLE001
            logger.warning(
                "brooklyn_bridge_park: failed to build event id=%r",
                row.get("id"),
                exc_info=True,
            )
    return events


class BrooklynBridgeParkSource(Source):
    """Brooklyn Bridge Park free family programming (WP REST + ACF)."""

    name = "brooklyn_bridge_park"
    display_name = "Brooklyn Bridge Park"
    max_pages = 12  # 671 posts / 100 per page = 7 in practice

    def __init__(
        self,
        *,
        events_url: str = EVENTS_URL,
        locations_url: str = LOCATIONS_URL,
        window_days: int = DEFAULT_WINDOW_DAYS,
        per_page: int = DEFAULT_PER_PAGE,
        page_delay: float = PAGE_DELAY_SECONDS,
        http_timeout: float = 30.0,
        max_pages: int | None = None,
    ):
        self._events_url = events_url
        self._locations_url = locations_url
        # Full collection re-fetch every run → missing-detection eligible.
        self.window_days = window_days
        self._per_page = per_page
        self._delay = page_delay
        self._timeout = http_timeout
        self._max_pages = max_pages if max_pages is not None else type(self).max_pages

    def _fetch_locations(self, client: httpx.Client) -> dict[int, str]:
        """id→name map for the maplocations post type. Best-effort: a failure
        just means venue names fall back to the park name."""
        locations: dict[int, str] = {}
        try:
            for page in (1, 2):
                resp = client.get(
                    self._locations_url,
                    params={"per_page": 100, "page": page, "_fields": "id,title"},
                )
                if resp.status_code == 400:  # past the last page
                    break
                resp.raise_for_status()
                rows = resp.json()
                for r in rows:
                    name = strip_html((r.get("title") or {}).get("rendered"))
                    if name:
                        locations[r["id"]] = name
                if len(rows) < 100:
                    break
                time_mod.sleep(self._delay)
        except Exception:  # noqa: BLE001
            logger.warning("brooklyn_bridge_park: locations fetch failed", exc_info=True)
        return locations

    def fetch(self) -> Iterable[Event]:
        today = datetime.now(NYC_TZ).date()
        horizon = today + timedelta(days=self.window_days)
        rows: list[dict[str, Any]] = []
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            locations = self._fetch_locations(client)
            page = 1
            while page <= self._max_pages:
                try:
                    resp = client.get(
                        self._events_url,
                        params={"per_page": self._per_page, "page": page},
                    )
                    if resp.status_code == 400:  # WP: invalid page = past the end
                        break
                    resp.raise_for_status()
                    batch = resp.json()
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "brooklyn_bridge_park: failed to fetch page %d", page, exc_info=True
                    )
                    break
                if not batch:
                    break
                rows.extend(batch)
                if len(batch) < self._per_page:
                    break
                page += 1
                time_mod.sleep(self._delay)

        total = 0
        for ev in parse_posts(rows, locations=locations):
            ev_date = ev.start_dt.astimezone(NYC_TZ).date()
            if today <= ev_date <= horizon:
                total += 1
                yield ev
        logger.info("brooklyn_bridge_park: yielded %d events", total)
