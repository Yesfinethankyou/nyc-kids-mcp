"""NYC Permitted Event Information (tvpp-9vvx) source.

**DISABLED 2026-07-12** (removed from ENABLED_SOURCES, module + tests kept):
maintainer wasn't using the permit rows — every one is low-confidence by
construction (no description, no URL), and nycgovparks_events now covers the
curated NYC Parks calendar (verified zero overlap, so this was pure noise on
top, not a duplicate). To re-enable, add NYCPermittedEventsSource back to
ENABLED_SOURCES and bump the opted-in count in
tests/test_missing_detection.py::test_full_window_sources_opt_in.

The Phase 1 spec originally named NYC Parks Events Listing (fudw-fgrp), but
that dataset is dead — last row is 2019-12, zero rows for any date >= today.
tvpp-9vvx is the live successor: a citywide permitting catalog updated daily.
It is broader than Parks events though — it includes parades, religious
gatherings, sport league field reservations, film shoots, marathons — so we
filter aggressively on agency + event_type + title patterns before yielding.

Quality is intentionally noisy in Phase 1; the architecture is what we're
proving. Phase 2 scrapers (Mommy Poppins, BPL, etc.) will provide the
curated kid-relevant signal.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..models import Borough, Event, Price, compute_id
from .base import Source

logger = logging.getLogger(__name__)

DATASET_URL = "https://data.cityofnewyork.us/resource/tvpp-9vvx.json"
NYC_TZ = ZoneInfo("America/New_York")
DEFAULT_DAYS_AHEAD = 60

# Permitting agency. Drops Police Dept road closures, MOME film permits, and
# Street Activity Permit Office street fairs that are usually adult-targeted.
KEPT_AGENCY = "Parks Department"

# Event types that can plausibly contain a parent-friendly event. Drops
# Sport-Youth / Sport-Adult (those are league field reservations, not
# events), Parade, Athletic Race/Tour, Religious Event, Production Event,
# Theater Load in and Load Outs, Clean-Up, Stationary Demonstration.
KEPT_EVENT_TYPES = {
    "Special Event",
    "Plaza Event",
    "Plaza Partner Event",
    "Block Party",
    "Open Culture",
    "Open Street Partner Event",
    "Single Block Festival",
    "Farmers Market",
    "Health Fair",
}

# Title regex blocklist. These almost always indicate non-parent-event rows.
# School-private and field-reservation patterns were added after Phase-1
# spot-checking surfaced lots of "PS 152 Field Day" and similar permits that
# look kid-friendly to a keyword matcher but are actually closed-to-public
# school events.
TITLE_BLOCKLIST = re.compile(
    r"\b("
    r"eid|prayer|jumu['’]?ah|salat|"
    r"load[\s\-]?in|load[\s\-]?out|"
    r"set[\s\-]?up|breakdown|"
    r"construction|"
    r"radio[\s\-]?control|rc[\s\-]?model|model[\s\-]?plane|model[\s\-]?helicopter|aircraft|"
    # Shape Up NYC is an adult fitness series (Zumba / cardio / intenSati),
    # not kid programming — drop it outright (issue #40).
    r"shape[\s\-]?up|"
    # NYC school identifiers: PS (elementary), I.S. (intermediate), JHS,
    # MS (middle), HS (high), plus a couple of program-specific markers
    # the user surfaced. Each requires \d+ so we don't false-positive on
    # words containing "ms" or "hs".
    r"ps[\s\-]?\d+|i\.?s\.?[\s\-]?\d+|jhs[\s\-]?\d+|ms[\s\-]?\d+|hs[\s\-]?\d+|bwls[\s\-]?\d+|bkg[\s\-]?\d+|"
    r"field\s+day|"             # almost always school field days, not public
    r"school|"                  # any title mentioning "school"
    r"private|"                 # private gatherings
    r"reservation|"             # field reservations, not events
    r"office|"                  # internal Parks office activities
    r"outreach"                 # internal community outreach
    r")\b",
    re.IGNORECASE,
)

# Literal "title is too generic to be useful" set.
USELESS_TITLES = {"miscellaneous", "celebration", "private event", "tbd", "event", "n/a"}

# Kid-relevant keyword → tag mapping. Per Phase 1 spec, plus tuning from
# real-ingest title spot-checks. An event row that doesn't match ANY keyword
# is treated as noise (private party / barbecue / press conference) and
# dropped at parse time — see _parse_row.
KID_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("story time", ("story time", "storytime", "story hour", "read aloud", "books at the park")),
    ("family", (
        "family", "families", "all ages", "intergenerational",
        "summer on the hudson",  # NYC's flagship free public series
    )),
    ("arts & crafts", ("craft", "art workshop", "paint", "draw", "make ", "diy ", "art class")),
    ("nature", ("nature", "garden", "stewardship", "outdoor", "wildlife",
                "birding", "tree ", "park tour")),
    ("music", (
        "music", "concert", "sing", "dance party", "dance class", "drum",
        "performance", "recital", "free concert",
    )),
    ("sports", ("kids sports", "youth sports", "junior", "little league", "play day", "sport ")),
    ("educational", ("workshop", "stem", "science", "history", "lesson", "class")),
    ("festival", ("festival", "fair ", "block party", "celebration")),
    ("best for kids", (
        # "kids"/"tots" prefix-match plurals; "kid "/"tot " (trailing space =
        # whole-word, see _kw_hit) match the singular without catching
        # "kidney"/"total" (issue #40).
        "kids", "kid ", "child", "tots", "tot ", "toddler", "preschool", "youth",
        # NOTE: "field day" was here but is now blocklisted — those are
        # school-private events, not public ones.
    )),
    ("movie", ("free movie", "movie night", "movies under", "outdoor movie")),
]

BOROUGH_MAP = {
    "manhattan": Borough.MANHATTAN,
    "brooklyn": Borough.BROOKLYN,
    "queens": Borough.QUEENS,
    "bronx": Borough.BRONX,
    "the bronx": Borough.BRONX,
    "staten island": Borough.STATEN_ISLAND,
}

_RAIN_DATE_RX = re.compile(r"\brain[\s\-]?date\b", re.IGNORECASE)

# Some tvpp-9vvx titles are prefixed with a literal date like "2026.05.14 May
# evening horseshoecrab monitoring". The prefix is the permit-author's note
# for which date the title text was written for; it isn't part of the event
# name and confuses Claude's reading.
_LEADING_DATE_RX = re.compile(r"^\s*\d{4}\.\d{1,2}\.\d{1,2}\s+")
_WHITESPACE_RX = re.compile(r"\s+")


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """Light upstream-junk cleanup applied before field extraction AND before
    raw_payload preservation. Mutates and returns row. Each cleaner is
    individually safe on missing/None fields.

    Cleaners:
    1. Sanity-check end_date_time: if end < start, clear it (some rows have
       e.g. end=14:00, start=18:00 — pure data-entry glitch).
    2. Trailing commas in community_board / police_precinct ("07," -> "07").
    3. Leading YYYY.MM.DD prefix in event_name.
    4. Collapse repeated whitespace in event_name.
    """
    # 1. end_dt < start_dt
    sdt = row.get("start_date_time")
    edt = row.get("end_date_time")
    if sdt and edt:
        try:
            if datetime.fromisoformat(edt) <= datetime.fromisoformat(sdt):
                logger.warning(
                    "nyc_permitted_events: end<=start on event_id=%s "
                    "(start=%s, end=%s); dropping end_date_time",
                    row.get("event_id"), sdt, edt,
                )
                row["end_date_time"] = None
        except ValueError:
            # Bad format — leave alone; _parse_row will skip via _parse_local_dt.
            pass

    # 2. Trailing commas
    for field in ("community_board", "police_precinct"):
        v = row.get(field)
        if isinstance(v, str):
            row[field] = v.rstrip(",").strip()

    # 3 + 4. Title prefix + whitespace collapse
    name = row.get("event_name")
    if isinstance(name, str):
        name = _LEADING_DATE_RX.sub("", name)
        name = _WHITESPACE_RX.sub(" ", name).strip()
        row["event_name"] = name

    return row

_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_MONTH_ABBREV = (
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
)


def _has_rain_date(title: str) -> bool:
    return bool(_RAIN_DATE_RX.search(title))


def _is_rain_date_occurrence(title: str, start_local_date) -> bool:
    """True iff a date mentioned in the title AFTER the phrase 'rain date'
    matches this row's start_dt — i.e., this row is the rain-day backup.

    Examples (with start_dt's local date):
        title="Health Fair May 16 and Rain Date May 30", start=May 30 -> True
        title="Health Fair May 16 and Rain Date May 30", start=May 16 -> False
        title="Juneteenth Movie Night RAIN DATE 6/20/2026", start=June 19 -> False
        title="Autism Walk and Resource Fair Rain Date", start=anything  -> False (no date after)
    """
    m = _RAIN_DATE_RX.search(title)
    if not m:
        return False
    after = title[m.end():].lower()
    month = _MONTH_NAMES[start_local_date.month - 1]
    abbr = _MONTH_ABBREV[start_local_date.month - 1]
    day = start_local_date.day
    patterns = (
        rf"\b{month}\s+0?{day}\b",            # "may 30"
        rf"\b{abbr}\.?\s+0?{day}\b",          # "may 30" / "may. 30"
        # "5/30", "05.30", "5/30/2026"
        rf"\b0?{start_local_date.month}[/.\-]0?{day}(?:[/.\-]\d{{2,4}})?\b",
        # "2026-05-30"
        rf"\b{start_local_date.year}[/.\-]0?{start_local_date.month}[/.\-]0?{day}\b",
    )
    return any(re.search(p, after) for p in patterns)


class NYCPermittedEventsSource(Source):
    """NYC Open Data 'NYC Permitted Event Information' (tvpp-9vvx)."""

    name = "nyc_permitted_events"
    display_name = "NYC Parks Permits"

    def __init__(
        self,
        dataset_url: str = DATASET_URL,
        *,
        days_ahead: int = DEFAULT_DAYS_AHEAD,
        http_timeout: float = 60.0,
    ):
        self._url = dataset_url
        self._days_ahead = days_ahead
        self.window_days = days_ahead  # full-window re-fetch: missing-detection eligible
        self._timeout = http_timeout

    def fetch(self) -> Iterable[Event]:
        for row in self._fetch_rows():
            try:
                ev = self._parse_row(row)
            except Exception as exc:  # noqa: BLE001 — never let one bad row kill ingest
                logger.warning(
                    "nyc_permitted_events: skipping row %s: %r",
                    row.get("event_id"),
                    exc,
                )
                continue
            if ev is None:
                continue
            # If the title says "rain date" and explicitly names a date that
            # matches this row's start_dt, this row is the rain-day backup —
            # drop it. The primary date row (if still future) survives.
            if _is_rain_date_occurrence(
                ev.title, ev.start_dt.astimezone(NYC_TZ).date()
            ):
                continue
            yield ev

    def _fetch_rows(self) -> list[dict[str, Any]]:
        now = datetime.now(NYC_TZ)
        until = now + timedelta(days=self._days_ahead)
        where = (
            f"event_agency='{KEPT_AGENCY}'"
            f" AND start_date_time > '{now.date().isoformat()}'"
            f" AND start_date_time < '{until.date().isoformat()}'"
        )
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                self._url,
                params={
                    "$where": where,
                    "$order": "start_date_time",
                    "$limit": 50000,
                },
            )
            resp.raise_for_status()
            return resp.json()

    def _parse_row(self, row: dict[str, Any]) -> Event | None:
        row = _clean_row(row)
        event_type = (row.get("event_type") or "").strip()
        if event_type not in KEPT_EVENT_TYPES:
            return None

        title = (row.get("event_name") or "").strip()
        if not title or len(title) < 4 or title.lower() in USELESS_TITLES:
            return None
        if TITLE_BLOCKLIST.search(title):
            return None

        start = _parse_local_dt(row.get("start_date_time"))
        if start is None:
            return None
        end = _parse_local_dt(row.get("end_date_time"))
        if end is not None and end <= start:
            # tvpp-9vvx occasionally has end < start (data glitch); drop the
            # bad end and keep the event with start-only.
            end = None

        borough = BOROUGH_MAP.get((row.get("event_borough") or "").strip().lower())
        venue = _clean_venue(row.get("event_location") or "")

        # event_id in this dataset is the PERMIT id, not the event-occurrence
        # id — a single permit covers all recurring occurrences (e.g. one
        # Little League permit = 31 rows, one per game). Use (permit_id,
        # start_dt) so each occurrence is its own DB row instead of collapsing.
        permit_id = row.get("event_id")
        if permit_id is not None:
            external_id = f"{permit_id}:{start.isoformat()}"
        else:
            external_id = None
        tags = _infer_tags(title, event_type)
        # Tag-empty rows are overwhelmingly noise in this dataset (private
        # picnics/parties/press conferences). Drop them at parse time so the
        # search experience stays clean; the source's stable IDs ensure that
        # if a row's title later gains a keyword match, it gets re-ingested.
        if not tags:
            return None

        return Event(
            id=compute_id(
                "nyc_permitted_events",
                external_id=external_id,
                title=title,
                venue=venue,
                date_iso=start.isoformat(),
            ),
            source="nyc_permitted_events",
            external_id=external_id,
            title=title,
            description=None,
            url=None,
            start_dt=start,
            end_dt=end,
            venue_name=venue or None,
            borough=borough,
            neighborhood=None,
            lat=None,
            lng=None,
            age_min=None,
            age_max=None,
            price=Price.UNKNOWN,  # dataset has no cost field
            tags=tags,
            # tvpp-9vvx is a rolling-window dataset (events drop ~30 days
            # after they end). Preserve the upstream JSON row so we can
            # debug field mapping or recover detail after upstream drops it.
            raw_payload=json.dumps(row, sort_keys=True),
        )


def _parse_local_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # SODA emits 'YYYY-MM-DDTHH:MM:SS.fff' with no tz; values are NYC local.
        naive = datetime.fromisoformat(s)
    except ValueError:
        return None
    if naive.tzinfo is not None:  # defensive; shouldn't happen with this dataset
        return naive
    return naive.replace(tzinfo=NYC_TZ)


def _clean_venue(venue: str) -> str:
    # Raw event_location often looks like "Marine Park: Hobby Field   ,Brookl"
    # Take only the part before the first colon (the park name) and strip the
    # trailing borough/comma noise.
    head = venue.split(":", 1)[0] if ":" in venue else venue
    return head.strip().rstrip(",").strip()


def _kw_hit(haystack: str, kw: str) -> bool:
    """Keyword match for the (inclusion-gating) tag inference.

    Bare substring matching admitted junk and fabricated tags — "craft" hit
    "air**craft**", "sing" hit "clo**sing**", "kid" hit "**kid**ney" — and here
    a keyword hit is what keeps the row (tag-empty rows are dropped), so a false
    positive both admits noise and mislabels it (issue #40).

    - A keyword given with a trailing space (e.g. "kid ", "sport ") is matched
      as a whole word (`\\bkw\\b`) — the historical guard against
      "sport"→"sportsmanship", extended to "kid"→"kidney".
    - Otherwise a leading word boundary (`\\bkw`) keeps useful prefix matches
      ("craft"→"crafts") while dropping mid-word hits ("aircraft").
    """
    if kw.endswith(" "):
        return re.search(rf"\b{re.escape(kw.strip())}\b", haystack) is not None
    return re.search(rf"\b{re.escape(kw)}", haystack) is not None


def _infer_tags(title: str, event_type: str) -> list[str]:
    haystack = f"{title.lower()} {event_type.lower()}"
    tags: list[str] = []
    for tag, keywords in KID_KEYWORDS:
        if any(_kw_hit(haystack, kw) for kw in keywords):
            tags.append(tag)
    return tags
