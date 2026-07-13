"""The MCP surface: FastMCP instance, the seven tools, and the event
projections they return.

This is the high-churn side of the server (Phase 3 keeps adding tools).
Everything auth-related — the bearer middleware, the OAuth shim, the rate
limiter — lives in auth.py; keep it that way so tool edits never touch the
"do not regress" security surface. server.py composes the two.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import config, db
from .models import Event

NYC_TZ = ZoneInfo("America/New_York")

# streamable_http_path="/": claude.ai treats the pasted connector URL as the
# MCP endpoint itself (it does NOT append /mcp); its initial probe goes to
# POST / and 404s otherwise.
#
# transport_security disabled: FastMCP auto-enables DNS-rebinding protection
# limited to localhost when settings.host is loopback, which 421s every
# request from a Tailscale Funnel hostname. DNS rebinding is only a threat
# when the server trusts implicit auth context (cookies); we require explicit
# Authorization headers (master token or OAuth-issued), so the protection
# adds no real security here and just blocks legitimate public hosts.
mcp = FastMCP(
    "nyc-events",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


def _venue_map_url(venue: str | None, borough: str | None) -> str | None:
    """Build a Google Maps lookup link for permit-source rows that have no
    real event URL. Lets Claude give the parent at least a clickable
    location, instead of just a name they have to retype."""
    if not venue:
        return None
    parts = [venue]
    if borough:
        parts.append(borough)
    parts.append("NY")
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(', '.join(parts))}"


_DESCRIPTION_PREVIEW_CHARS = 200

# How long an event must be continuously missing from its source's ingest
# before tools surface possibly_cancelled. The number lives in db.py
# (db.MISSING_GRACE_HOURS) so the dashboard's health counts use the same
# threshold without importing this module.
_MISSING_GRACE_HOURS = db.MISSING_GRACE_HOURS


def _possibly_cancelled(ev: Event) -> bool:
    if ev.missing_since is None:
        return False
    return datetime.now(UTC) - ev.missing_since > timedelta(hours=_MISSING_GRACE_HOURS)


def _truncate(s: str | None, max_len: int = _DESCRIPTION_PREVIEW_CHARS) -> str | None:
    if s is None or len(s) <= max_len:
        return s
    return s[:max_len].rstrip() + "…"


def _event_summary(ev: Event) -> dict[str, Any]:
    """Listing-tool projection. Pared down to what Claude needs to rank +
    decide, with description truncated for token efficiency. Drill into the
    full record via get_event_detail(event_id) or get_event_raw(event_id)."""
    low_confidence = ev.description is None and ev.url is None
    return {
        "event_id": ev.id,
        "title": ev.title,
        "when_local": ev.start_dt.astimezone(NYC_TZ).isoformat(),
        # end_local rides along so a noon–4pm event presents as a range
        # instead of a bare "12:00" (which reads as ambiguous or midnight).
        # None when the source provides no end time.
        "end_local": (
            ev.end_dt.astimezone(NYC_TZ).isoformat() if ev.end_dt else None
        ),
        "borough": ev.borough.value if ev.borough else None,
        "neighborhood": ev.neighborhood,
        "venue": ev.venue_name,
        "price": ev.price.value,
        "tags": ev.tags,
        "url": ev.url,
        "venue_map_url": _venue_map_url(
            ev.venue_name, ev.borough.value if ev.borough else None
        ),
        "description": _truncate(ev.description),
        "low_confidence": low_confidence,
        "possibly_cancelled": _possibly_cancelled(ev),
    }


def _event_detail(ev: Event) -> dict[str, Any]:
    """Full normalized projection for the get_event_detail tool. Includes
    everything in the summary plus the untruncated description, age range,
    lat/lng, source, and the upstream external_id — but NOT the raw_payload
    (use get_event_raw for that)."""
    low_confidence = ev.description is None and ev.url is None
    return {
        "event_id": ev.id,
        "external_id": ev.external_id,
        "source": ev.source,
        "title": ev.title,
        "description": ev.description,  # untruncated
        "when_local": ev.start_dt.astimezone(NYC_TZ).isoformat(),
        "end_local": (
            ev.end_dt.astimezone(NYC_TZ).isoformat() if ev.end_dt else None
        ),
        "borough": ev.borough.value if ev.borough else None,
        "venue": ev.venue_name,
        "neighborhood": ev.neighborhood,
        "lat": ev.lat,
        "lng": ev.lng,
        "price": ev.price.value,
        "age_min": ev.age_min,
        "age_max": ev.age_max,
        "tags": ev.tags,
        "url": ev.url,
        "venue_map_url": _venue_map_url(
            ev.venue_name, ev.borough.value if ev.borough else None
        ),
        "low_confidence": low_confidence,
        "possibly_cancelled": _possibly_cancelled(ev),
    }


def _normalize_borough(b: str | None) -> str | None:
    if not b:
        return None
    table = {
        "manhattan": "Manhattan",
        "brooklyn": "Brooklyn",
        "queens": "Queens",
        "bronx": "Bronx",
        "the bronx": "Bronx",
        "staten island": "Staten Island",
        "si": "Staten Island",
    }
    return table.get(b.strip().lower(), b.strip().title())


def _local_date(value: str) -> date:
    """Parse a YYYY-MM-DD string as a calendar date, with a clear error."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc


@mcp.tool()
def search_events(
    query: str | None = None,
    borough: str | None = None,
    neighborhood: str | None = None,
    age: int | None = None,
    free_only: bool = False,
    exclude_low_confidence: bool = False,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    days_ahead: int = 14,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Search NYC family-friendly events with optional filters.

    Use this for free-text questions like "outdoor music in Brooklyn" or
    "museum activities for a 4-year-old". Combine with the filter args to
    narrow down. Results are ordered by start time.

    Each result has a `low_confidence: bool` flag — true means the row came
    from a permit-style source (no description, no URL) and may not be a
    public-facing event. Surface that uncertainty to the user instead of
    assuming the event is attendable. Pass `exclude_low_confidence=True` to
    drop those rows entirely when the user wants only curated, attendable
    events.

    Each result also has a `venue_map_url` field with a Google Maps link
    for the venue. If `url` is null (most permit-source rows are),
    `venue_map_url` is the best clickable destination to give the user.

    `possibly_cancelled: true` means the event vanished from its source's
    feed across multiple recent ingests — it may have been cancelled
    upstream. Still show it if relevant, but warn the user to confirm with
    the venue (via `url` or `venue_map_url`) before making plans.

    Args:
        query: optional free-text search over title, description, venue,
            neighborhood, and tags. Prefix-matched, so "muse" matches "museum".
        borough: Manhattan, Brooklyn, Queens, Bronx, or Staten Island.
        neighborhood: filter to a neighborhood (e.g. "Williamsburg",
            "Crown Heights"). Case-insensitive substring, so a short name
            matches the fuller official neighborhood labels it appears in.
        age: kid's age in years. Returns events whose [age_min, age_max]
            window includes this age, plus events without a declared range.
        free_only: if True, only events explicitly flagged free.
        exclude_low_confidence: if True, drop permit-style rows that have no
            description and no URL (the `low_confidence` rows). Use this when
            the user only wants curated, clearly attendable events.
        source: restrict to a single source id (see list_sources / list_facets
            for the available ids, e.g. "domino_park").
        start_date: YYYY-MM-DD (America/New_York). Window starts at 00:00 on
            this date instead of now — use it to look at a specific future
            date range. Defaults to now.
        end_date: YYYY-MM-DD (America/New_York). Window ends at 23:59:59 on
            this date. When omitted, the window ends `days_ahead` days after
            the start. Provide both start_date and end_date for an explicit
            range like a planned visit ("Aug 12–15").
        days_ahead: width of the window when end_date is omitted; counted from
            the start (default 14, max 365).
        limit: max events to return (default 15, max 50). Use
            get_event_detail(event_id) to drill into a specific result.
    """
    days_ahead = min(days_ahead, 365)
    limit = max(1, min(limit, 50))
    now = datetime.now(NYC_TZ)
    start = (
        datetime.combine(_local_date(start_date), time(0, 0), NYC_TZ)
        if start_date
        else now
    )
    if end_date:
        end = datetime.combine(_local_date(end_date), time(23, 59, 59), NYC_TZ)
    else:
        end = start + timedelta(days=days_ahead)
    if end < start:
        raise ValueError(
            f"end_date {end_date!r} is before the window start "
            f"({start_date!r} or now)"
        )
    with db.connect_events(config.DB_PATH) as conn:
        events = db.search(
            conn,
            query=query,
            borough=_normalize_borough(borough),
            neighborhood=neighborhood,
            age=age,
            free_only=free_only,
            source=source,
            exclude_low_confidence=exclude_low_confidence,
            start_after=start.astimezone(UTC),
            start_before=end.astimezone(UTC),
            limit=limit,
        )
    return [_event_summary(e) for e in events]


def _weekend_window(now: datetime) -> tuple[datetime, datetime]:
    """Window for events_this_weekend: Saturday 00:00 through Sunday 23:59
    local of the current/upcoming weekend. If `now` is already inside the
    weekend, the window starts at `now` — never earlier, never midweek."""
    days_to_sunday = (6 - now.weekday()) % 7  # weekday: Mon=0..Sun=6
    sunday = (now + timedelta(days=days_to_sunday)).date()
    saturday_start = datetime.combine(
        sunday - timedelta(days=1), time(0, 0), NYC_TZ
    )
    sunday_end = datetime.combine(sunday, time(23, 59, 59), NYC_TZ)
    return max(now, saturday_start), sunday_end


@mcp.tool()
def events_this_weekend(
    borough: str | None = None,
    age: int | None = None,
    free_only: bool = False,
    exclude_low_confidence: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Events happening THIS weekend in NYC.

    Window: Saturday 00:00 through Sunday 23:59 local of the current or
    upcoming weekend. If today is Saturday or Sunday, the window starts
    now (includes the rest of today). Weekday events are never included.

    Args:
        borough: Manhattan, Brooklyn, Queens, Bronx, or Staten Island.
        age: kid's age in years (see search_events for matching semantics).
        free_only: if True, only events explicitly flagged free.
        exclude_low_confidence: if True, drop permit-style rows with no
            description and no URL (the `low_confidence` rows), leaving only
            curated, clearly attendable events.
        limit: max events to return (default 10, max 50). Use
            get_event_detail(event_id) to drill into a specific result.
    """
    limit = max(1, min(limit, 50))
    window_start, sunday_end = _weekend_window(datetime.now(NYC_TZ))
    with db.connect_events(config.DB_PATH) as conn:
        events = db.search(
            conn,
            borough=_normalize_borough(borough),
            age=age,
            free_only=free_only,
            exclude_low_confidence=exclude_low_confidence,
            start_after=window_start.astimezone(UTC),
            start_before=sunday_end.astimezone(UTC),
            limit=limit,
        )
    return [_event_summary(e) for e in events]


@mcp.tool()
def events_on_date(
    date: str,
    borough: str | None = None,
    age: int | None = None,
    free_only: bool = False,
    exclude_low_confidence: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Events on a specific local NYC date.

    Args:
        date: YYYY-MM-DD (interpreted as America/New_York local).
        borough: Manhattan, Brooklyn, Queens, Bronx, or Staten Island.
        age: kid's age in years.
        free_only: if True, only events explicitly flagged free.
        exclude_low_confidence: if True, drop permit-style rows with no
            description and no URL (the `low_confidence` rows), leaving only
            curated, clearly attendable events.
        limit: max events to return (default 10, max 50). Use
            get_event_detail(event_id) to drill into a specific result.
    """
    limit = max(1, min(limit, 50))
    d = _local_date(date)
    day_start = datetime.combine(d, time(0, 0), NYC_TZ)
    day_end = datetime.combine(d, time(23, 59, 59), NYC_TZ)
    with db.connect_events(config.DB_PATH) as conn:
        events = db.search(
            conn,
            borough=_normalize_borough(borough),
            age=age,
            free_only=free_only,
            exclude_low_confidence=exclude_low_confidence,
            start_after=day_start.astimezone(UTC),
            start_before=day_end.astimezone(UTC),
            limit=limit,
        )
    return [_event_summary(e) for e in events]


@mcp.tool()
def get_event_detail(event_id: str) -> dict[str, Any] | None:
    """Return the full normalized record for one event.

    Listing tools (search_events, events_this_weekend, events_on_date) trim
    fields and truncate descriptions for token efficiency. Call this tool
    with the `event_id` from a listing result when the user drills into a
    specific event and you need everything: full description, lat/lng,
    age range, and the upstream external_id.

    Returns None if the event_id isn't found. For the original upstream
    payload, see get_event_raw instead.
    """
    with db.connect_events(config.DB_PATH) as conn:
        ev = db.get_event_by_id(conn, event_id)
    return _event_detail(ev) if ev is not None else None


@mcp.tool()
def get_event_raw(event_id: str) -> dict[str, Any] | None:
    """Return the original upstream API payload for one event, before
    normalization.

    Useful for debugging field-mapping issues, recovering data that has
    aged out of a rolling-window upstream dataset (e.g. NYC's tvpp-9vvx
    keeps only ~30 days), or confirming what a specific source actually
    sent us.

    Pass the `event_id` you got from a listing tool (search_events,
    events_this_weekend, events_on_date). Returns None if the event_id
    isn't found, or if the row was ingested before raw_payload tracking
    existed (older rows will gain a payload on the next nightly re-ingest
    while they're still in the upstream window).
    """
    with db.connect_events(config.DB_PATH) as conn:
        ev = db.get_event_by_id(conn, event_id)
    if ev is None or ev.raw_payload is None:
        return None
    try:
        return json.loads(ev.raw_payload)
    except json.JSONDecodeError:
        return None


@mcp.tool()
def list_sources() -> list[dict[str, Any]]:
    """List ingested event sources with counts and freshness.

    Returns one row per source with event_count, earliest_event, latest_event,
    and last_seen. Use this for data-health questions ("is the catalog
    current?", "which source is stale/empty?") — not for finding events. To
    discover valid search_events filter values (including source ids), prefer
    list_facets.
    """
    with db.connect_events(config.DB_PATH) as conn:
        return db.list_sources(conn)


@mcp.tool()
def list_facets() -> dict[str, list[str]]:
    """List the distinct filter values currently present in the catalog.

    Returns the valid values for the main search_events filters, drawn from
    the events actually ingested right now:
      - boroughs:      borough names in use
      - neighborhoods: neighborhood labels in use (search_events filters these
                       as a case-insensitive substring, so a partial label works)
      - tags:          all tags applied across events
      - sources:       source ids (pass one back as search_events `source`)

    Call this first when you need to translate a vague user phrase into a valid
    filter value (e.g. which neighborhoods or tags exist) instead of guessing.
    """
    with db.connect_events(config.DB_PATH) as conn:
        return db.list_facets(conn)
