"""Read-only tailnet dashboard: connector/ingest health plus an event
browse/filter page.

A separate process from the MCP server, on its own port
(`python -m nyc_events.dashboard`, default 8766 via config.DASHBOARD_PORT),
exposed via `tailscale serve` (tailnet-only) — NEVER Tailscale Funnel.
Tailnet membership is the auth; there is no login and must never be one.

Read-only by construction, enforced twice:
- every DB access goes through db.connect_events_ro (a `mode=ro` SQLite URI —
  this process physically cannot write, and never runs init_events/DDL);
- every route is GET-only (tests assert no other methods exist).

Import rule: this module imports db, config, and the sources registry only.
Importing auth or tools from here is the same red flag as a tool PR touching
auth.py — the whole design exists so the browser surface can't reach the
security surface (or oauth.db, which this process never opens).

HTML is rendered with stdlib f-strings + html.escape on every interpolated
value (same approach as the consent page in auth.py). Event fields are
scraped from the public web — treat every one as attacker-influenced. No
JS frameworks, no CDN assets: tailnet pages shouldn't leak to third-party
hosts.
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote_plus, urlencode
from zoneinfo import ZoneInfo

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import Route

from . import config, db
from .sources import ENABLED_SOURCES

NYC_TZ = ZoneInfo("America/New_York")

# `events.source` stores Source.name — a stable internal id (used in
# compute_id, not meant for display; see sources/base.py). This maps it to
# the human venue name for rendering. Falls back to the raw internal name for
# anything unmapped, so a new source (or one that's since been disabled, like
# nyc_permitted_events) never breaks rendering just because this dict lags.
_SOURCE_LABELS: dict[str, str] = {
    "ny_transit_museum": "New York Transit Museum",
    "brooklyn_army_terminal": "Brooklyn Army Terminal",
    "bk_childrens_museum": "Brooklyn Children's Museum",
    "greenwood_cemetery": "Green-Wood Cemetery",
    "prospect_park": "Prospect Park Alliance",
    "industry_city": "Industry City",
    "governors_island": "Governors Island",
    "domino_park": "Domino Park",
    "si_childrens_museum": "Staten Island Children's Museum",
    "bbg": "Brooklyn Botanic Garden",
    "brooklyn_bridge_park": "Brooklyn Bridge Park",
    "bpl": "Brooklyn Public Library",
    "nycgovparks_events": "NYC Parks",
    "new_york_family": "New York Family",
    "mommy_poppins": "Mommy Poppins",
    "nyc_permitted_events": "NYC Parks Permits (retired)",
    "timeout_nykids": "Time Out New York Kids",
}


def _source_label(name: str | None) -> str:
    if name is None:
        return ""
    return _SOURCE_LABELS.get(name, name)

# Staleness thresholds for MAX(last_seen) highlighting on the health page.
# WARN = one missed nightly run (same 30h grace the MCP tools use for
# possibly_cancelled — one number, one home: db.MISSING_GRACE_HOURS);
# BAD = two missed runs.
_STALE_WARN_HOURS = db.MISSING_GRACE_HOURS
_STALE_BAD_HOURS = 54

# Browse-page limit: browsers scan fine at 200 rows; the 50 cap in tools.py
# is an LLM token budget, which doesn't apply here.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# Deliberately craigslist-plain: default underlined blue/purple links, no
# boxes or badges, horizontal rules instead of cell borders. The only
# "modern" touches are load-bearing, not decorative: a sticky header row and
# subtle zebra striping so a 200-row table stays scannable.
_STYLE = """
body { font-family: Arial, Helvetica, sans-serif; font-size: 0.85rem;
       margin: 1.25rem; color: #222; background: #fff; }
h1 { font-size: 1.15rem; } h2 { font-size: 1rem; }
table { border-collapse: collapse; font-size: 0.85rem; }
th, td { border: 0; border-bottom: 1px solid #e6e6e6;
         padding: 0.25rem 0.9rem 0.25rem 0; text-align: left;
         vertical-align: top; }
th { position: sticky; top: 0; background: #fff;
     border-bottom: 1px solid #888; }
tr:nth-child(even) { background: #f7f7f7; }
td.when { white-space: nowrap; }
.ok { background: #e6f4e6; }
.warn { background: #fff3cd; }
.bad { background: #f8d7da; }
.muted { color: #777; }
.strip { margin: 0.75rem 0; }
.strip span { display: inline-block; margin-right: 1.25rem; }
.strip b { font-size: 1.05rem; }
form.filters { margin: 0.75rem 0; }
form.filters label { display: inline-block; margin: 0.15rem 0.9rem 0.15rem 0; }
.presets { margin: 0.25rem 0 0.5rem; }
.error { color: #a00; font-weight: bold; }
nav a { margin-right: 1rem; }
dt { font-weight: bold; margin-top: 0.5rem; }
dd { margin-left: 0; }
"""


# Same header set as the consent page in auth.py: event fields are scraped
# from the public web, so the CSP is defense-in-depth against any escaping
# slip (script-src 'none' via default-src also blocks javascript: navigation),
# and Referrer-Policy stops the private *.ts.net dashboard hostname leaking
# to venue sites when someone clicks an event link.
_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "  # inline <style> in the template
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
}


def _esc(value: object) -> str:
    """Escape any value for HTML interpolation; None renders as an em dash."""
    if value is None:
        return "<span class='muted'>—</span>"
    return html.escape(str(value))


def _safe_url(url: str | None) -> str | None:
    """Gate a scraped URL before rendering it as an anchor: html.escape stops
    attribute breakout but not a javascript:/data: scheme, which would execute
    on click. Anything that isn't plain http(s) renders as text, not a link."""
    if url and url.lower().startswith(("https://", "http://")):
        return url
    return None


def _page(title: str, body: str, *, status: int = 200, refresh: int | None = None) -> HTMLResponse:
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    doc = (
        "<!doctype html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{meta}<title>{html.escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body>"
        '<nav><a href="/">Health</a><a href="/events">Browse events</a></nav>'
        f"{body}</body></html>"
    )
    return HTMLResponse(doc, status_code=status, headers=_SECURITY_HEADERS)


def _is_missing_db(exc: sqlite3.OperationalError) -> bool:
    """True for the two errors a mode=ro open of an absent/uninitialized DB
    produces. Anything else re-raises — mislabeling a real query failure as
    "no database yet" would hide it."""
    msg = str(exc).lower()
    return "unable to open database file" in msg or "no such table" in msg


def _no_db_page() -> HTMLResponse:
    return _page(
        "nyc-events dashboard",
        "<h1>No database yet</h1>"
        "<p>The events database doesn't exist or has no tables — has the "
        "ingest run at least once? (This dashboard is read-only and never "
        "creates it.)</p>",
    )


def _error_page(message: str) -> HTMLResponse:
    return _page(
        "nyc-events dashboard — error",
        f"<h1>Bad request</h1><p class='error'>{html.escape(message)}</p>"
        '<p><a href="/events">Back to browse</a></p>',
        status=400,
    )


def _local(iso: str | None) -> str | None:
    """Render a stored UTC ISO timestamp as NYC local, minute precision."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(NYC_TZ).strftime("%Y-%m-%d %H:%M")


def _local_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} must be YYYY-MM-DD, got {value!r}") from None


def _venue_map_url(venue: str | None, borough: str | None) -> str | None:
    # Mirrors tools._venue_map_url (not imported — see the module-docstring
    # import rule): a Google Maps lookup for rows with no real event URL.
    if not venue:
        return None
    parts = [venue]
    if borough:
        parts.append(borough)
    parts.append("NY")
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(', '.join(parts))}"


def _possibly_cancelled(missing_since: datetime | None, now: datetime) -> bool:
    if missing_since is None:
        return False
    return now - missing_since > timedelta(hours=db.MISSING_GRACE_HOURS)


def _staleness_class(last_seen: str | None, now: datetime) -> str:
    if last_seen is None:
        return "bad"
    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return "bad"
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    age = now - seen
    if age > timedelta(hours=_STALE_BAD_HOURS):
        return "bad"
    if age > timedelta(hours=_STALE_WARN_HOURS):
        return "warn"
    return "ok"


# ---- routes ------------------------------------------------------------------


async def healthz(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def health_page(_: Request) -> HTMLResponse:
    now = datetime.now(UTC)
    registered = [cls.name for cls in ENABLED_SOURCES]
    try:
        with db.connect_events_ro(config.DB_PATH) as conn:
            rows = db.source_health(conn, now, registered)
            stats = db.catalog_stats(conn, now)
    except sqlite3.OperationalError as exc:
        if not _is_missing_db(exc):
            raise
        return _no_db_page()

    try:
        db_size_mb = f"{os.path.getsize(config.DB_PATH) / 1_048_576:.1f} MB"
    except OSError:
        db_size_mb = "?"

    strip = (
        "<div class='strip'>"
        f"<span><b>{stats['total_events']}</b> events</span>"
        f"<span><b>{stats['future_events']}</b> future</span>"
        f"<span><b>{stats['neighborhood_pct']}%</b> neighborhood-coded</span>"
        f"<span><b>{stats['geocode_cache_rows']}</b> geocode cache rows</span>"
        f"<span><b>{_esc(db_size_mb)}</b> DB size</span>"
        f"<span class='muted'>rendered {_esc(_local(now.isoformat()))} NYC</span>"
        "</div>"
    )

    trs = []
    for r in rows:
        # A registered source with zero rows is the "scraper broke" signal —
        # always red, regardless of last_seen math.
        cls = "bad" if (r["registered"] and r["event_count"] == 0) else \
            _staleness_class(r["last_seen"], now)
        run_cls = {"ok": "ok"}.get(r["last_run_outcome"] or "", "")
        if r["last_run_outcome"] and r["last_run_outcome"] != "ok":
            run_cls = "bad"
        run = (
            f"<td class='{run_cls}'>{_esc(r['last_run_outcome'])} "
            f"<span class='muted'>{_esc(_local(r['last_run_finished_at']))}"
            f" · fetched {_esc(r['last_run_fetched'])}</span></td>"
        )
        name = _esc(_source_label(r["source"]))
        if not r["registered"]:
            name += " <span class='muted'>(not registered)</span>"
        trs.append(
            f"<tr><td>{name}</td>"
            f"<td>{r['event_count']}</td><td>{r['future_count']}</td>"
            f"<td>{_esc(_local(r['earliest_event']))}</td>"
            f"<td>{_esc(_local(r['latest_event']))}</td>"
            f"<td class='{cls}'>{_esc(_local(r['last_seen']))}</td>"
            f"{run}"
            f"<td>{r['flagged_missing']}</td><td>{r['low_confidence']}</td></tr>"
        )
    table = (
        "<table><tr><th>Source</th><th>Events</th><th>Future</th>"
        "<th>Earliest</th><th>Latest</th><th>Last seen</th>"
        "<th>Last ingest run</th><th>Flagged missing</th>"
        "<th>Low confidence</th></tr>" + "".join(trs) + "</table>"
    )
    return _page(
        "nyc-events — source health",
        f"<h1>Source health</h1>{strip}{table}",
        refresh=300,
    )


def _parse_browse_params(params) -> dict:
    """Map query params onto db.search kwargs. Raises ValueError with a
    user-renderable message on bad input — mirrors tools.py validation.

    borough/neighborhood/source are multi-selects: the browser submits one
    query-param instance per selected `<option>` (`borough=Brooklyn&
    borough=Queens`), read via getlist(). db.search accepts the resulting
    list directly (see its docstring for the multi-value semantics)."""
    kwargs: dict = {}
    q = (params.get("q") or "").strip()
    if q:
        kwargs["query"] = q
    for key in ("borough", "neighborhood", "source"):
        values = [v.strip() for v in params.getlist(key) if v.strip()]
        if values:
            kwargs[key] = values
    age_raw = (params.get("age") or "").strip()
    if age_raw:
        try:
            kwargs["age"] = int(age_raw)
        except ValueError:
            raise ValueError(f"age must be a number, got {age_raw!r}") from None
    kwargs["free_only"] = params.get("free_only") == "1"
    kwargs["exclude_low_confidence"] = params.get("exclude_low_confidence") == "1"

    now = datetime.now(NYC_TZ)
    start_raw = (params.get("start_date") or "").strip()
    end_raw = (params.get("end_date") or "").strip()
    start = (
        datetime.combine(_local_date(start_raw, "start_date"), time(0, 0), NYC_TZ)
        if start_raw
        else now
    )
    end = (
        datetime.combine(_local_date(end_raw, "end_date"), time(23, 59, 59), NYC_TZ)
        if end_raw
        else None
    )
    if end is not None and end < start:
        raise ValueError(f"end_date {end_raw!r} is before the window start")
    kwargs["start_after"] = start.astimezone(UTC)
    if end is not None:
        kwargs["start_before"] = end.astimezone(UTC)

    limit_raw = (params.get("limit") or "").strip()
    if limit_raw:
        try:
            limit = int(limit_raw)
        except ValueError:
            raise ValueError(f"limit must be a number, got {limit_raw!r}") from None
    else:
        limit = _DEFAULT_LIMIT
    kwargs["limit"] = max(1, min(limit, _MAX_LIMIT))
    return kwargs


def _multi_select(
    name: str,
    values: list[str],
    selected: list[str],
    *,
    size: int,
    label_fn: Callable[[str], str] | None = None,
) -> str:
    """A native <select multiple> — no "any" placeholder needed, since no
    selection already means "any" (ctrl/cmd-click, or shift-click a range,
    to pick more than one; no JS required). `label_fn`, when given, renders a
    human-readable option text while the submitted `value` stays the raw
    internal identifier the filter actually matches on."""
    label_fn = label_fn or (lambda v: v)
    opts = "".join(
        f"<option value='{html.escape(v, quote=True)}'"
        f"{' selected' if v in selected else ''}>{html.escape(label_fn(v))}</option>"
        for v in values
    )
    # size floor of 2: at size=1 a <select multiple> renders like a tiny
    # number-spinner rather than a listbox, which reads as broken.
    rows = min(max(len(values), 2), size)
    return f"<select name='{name}' multiple size='{rows}'>{opts}</select>"


def _filtered_neighborhoods(values: list[str], selected: list[str], query: str) -> list[str]:
    """Narrow the neighborhood option list to a case-insensitive substring
    match on `query` — a search box for the dropdown without JS (the
    dashboard's CSP has no script-src at all; see the module docstring).
    Already-selected values are always kept, even when they don't match the
    current search text, so typing a new search never silently drops an
    existing selection out of the option list."""
    q = query.strip().lower()
    if not q:
        return values
    keep = set(selected)
    return [v for v in values if q in v.lower() or v in keep]


# Non-date filter params carried into the preset links, so "this weekend"
# narrows the current filter set instead of resetting it. The multi-select
# fields need getlist(), not get() — a plain get() would silently drop every
# selection past the first.
_CARRY_PARAMS = ("q", "nbhd_q", "age", "free_only", "exclude_low_confidence", "limit")
_CARRY_MULTI_PARAMS = ("borough", "neighborhood", "source")


def _preset_links(params) -> str:
    """Quick date-window links. Weekend math mirrors tools._weekend_window
    (not imported — see the module-docstring import rule): the current or
    upcoming Sat–Sun, starting today if today already is the weekend."""
    keep = [(k, v) for k in _CARRY_PARAMS if (v := (params.get(k) or "").strip())]
    for k in _CARRY_MULTI_PARAMS:
        keep.extend((k, v) for v in params.getlist(k) if v.strip())
    today = datetime.now(NYC_TZ).date()
    sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    saturday = max(today, sunday - timedelta(days=1))
    presets = [
        ("today", today, today),
        ("this weekend", saturday, sunday),
        ("next 7 days", today, today + timedelta(days=7)),
    ]
    links = []
    for label, start, end in presets:
        qs = urlencode(
            keep + [("start_date", start.isoformat()), ("end_date", end.isoformat())]
        )
        links.append(f"<a href='/events?{html.escape(qs, quote=True)}'>{label}</a>")
    return f"<p class='presets'>{' · '.join(links)}</p>"


def _browse_form(params, facets: dict[str, list[str]]) -> str:
    def val(key: str) -> str:
        return html.escape(params.get(key) or "", quote=True)

    def checked(key: str) -> str:
        return " checked" if params.get(key) == "1" else ""

    limit_sel = params.get("limit") or str(_DEFAULT_LIMIT)
    limit_opts = "".join(
        f"<option value='{n}'{' selected' if str(n) == limit_sel else ''}>{n}</option>"
        for n in (25, 50, 100, 200)
    )
    borough_select = _multi_select(
        "borough", facets["boroughs"], params.getlist("borough"), size=5
    )
    source_values = sorted(facets["sources"], key=lambda s: _source_label(s).lower())
    source_select = _multi_select(
        "source", source_values, params.getlist("source"), size=6, label_fn=_source_label
    )
    nbhd_selected = params.getlist("neighborhood")
    nbhd_options = _filtered_neighborhoods(
        facets["neighborhoods"], nbhd_selected, params.get("nbhd_q") or ""
    )
    nbhd_select = _multi_select("neighborhood", nbhd_options, nbhd_selected, size=6)
    xlc = checked("exclude_low_confidence")
    return (
        "<form class='filters' method='get' action='/events'>"
        f"<label>Text <input name='q' value='{val('q')}'></label>"
        f"<label>Age <input name='age' size='3' value='{val('age')}'></label>"
        "<label>From <input name='start_date' type='date'"
        f" value='{val('start_date')}'></label>"
        f"<label>To <input name='end_date' type='date' value='{val('end_date')}'></label>"
        "<label><input type='checkbox' name='free_only' value='1'"
        f"{checked('free_only')}> free only</label>"
        "<label><input type='checkbox' name='exclude_low_confidence' value='1'"
        f"{xlc}> hide low-confidence</label>"
        f"<label>Limit <select name='limit'>{limit_opts}</select></label>"
        "<br>"
        "<label>Borough <span class='muted'>(ctrl/cmd-click for multiple)"
        f"</span><br>{borough_select}</label>"
        "<label>Neighborhood <span class='muted'>(search narrows the list;"
        " Filter to apply)</span><br>"
        f"<input name='nbhd_q' placeholder='search…' value='{val('nbhd_q')}'><br>"
        f"{nbhd_select}</label>"
        f"<label>Source<br>{source_select}</label>"
        "<br>"
        "<button type='submit'>Filter</button> "
        "<a href='/events'>reset</a>"
        "</form>"
    )


async def events_page(request: Request) -> HTMLResponse:
    params = request.query_params
    try:
        kwargs = _parse_browse_params(params)
    except ValueError as exc:
        return _error_page(str(exc))
    now = datetime.now(UTC)
    try:
        with db.connect_events_ro(config.DB_PATH) as conn:
            facets = db.list_facets(conn)
            events = db.search(conn, **kwargs)
    except sqlite3.OperationalError as exc:
        if not _is_missing_db(exc):
            raise
        return _no_db_page()

    trs = []
    for ev in events:
        flags = []
        if ev.description is None and ev.url is None:
            flags.append("low confidence")
        if _possibly_cancelled(ev.missing_since, now):
            flags.append("possibly cancelled")
        links = []
        safe_url = _safe_url(ev.url)
        if safe_url:
            links.append(
                f"<a href='{html.escape(safe_url, quote=True)}'"
                " rel='noopener noreferrer'>event</a>"
            )
        elif ev.url:
            links.append(_esc(ev.url))  # non-http(s) scheme: show, don't link
        map_url = _venue_map_url(ev.venue_name, ev.borough.value if ev.borough else None)
        if map_url:
            links.append(
                f"<a href='{html.escape(map_url, quote=True)}'"
                " rel='noopener noreferrer'>map</a>"
            )
        # Truncated description as a hover tooltip — a peek without a click
        # through to the detail page. Scraped text, so escaped like the rest.
        desc = (ev.description or "").strip()
        tip = f" title='{html.escape(desc[:200], quote=True)}'" if desc else ""
        start = ev.start_dt.astimezone(NYC_TZ)
        when = start.strftime("%Y-%m-%d %H:%M")
        if ev.end_dt:
            end = ev.end_dt.astimezone(NYC_TZ)
            # Same-day range compresses to "12:00–16:00"; a multi-day event
            # keeps the full end stamp so the rollover is visible.
            if end.date() == start.date():
                when += f"–{end.strftime('%H:%M')}"
            else:
                when += f" – {end.strftime('%Y-%m-%d %H:%M')}"
        trs.append(
            "<tr>"
            f"<td class='when'>{_esc(when)}</td>"
            f"<td><a href='/event/{html.escape(ev.id, quote=True)}'{tip}>{_esc(ev.title)}</a></td>"
            f"<td>{_esc(ev.venue_name)}</td>"
            f"<td>{_esc(ev.neighborhood)}</td>"
            f"<td>{_esc(ev.borough.value if ev.borough else None)}</td>"
            f"<td>{_esc(ev.price.value)}</td>"
            f"<td>{_esc(', '.join(ev.tags))}</td>"
            f"<td>{_esc(_source_label(ev.source))}</td>"
            f"<td>{_esc(', '.join(flags) or None)}</td>"
            f"<td>{' '.join(links)}</td>"
            "</tr>"
        )
    table = (
        "<table><tr><th>When (NYC)</th><th>Title</th><th>Venue</th>"
        "<th>Neighborhood</th><th>Borough</th><th>Price</th><th>Tags</th>"
        "<th>Source</th><th>Flags</th><th>Links</th></tr>" + "".join(trs) + "</table>"
        if events
        else "<p class='muted'>No events match.</p>"
    )
    # At exactly `limit` rows the count is ambiguous — say so rather than
    # letting "50 result(s)" read as "50 matches total".
    count = f"{len(events)} result(s)"
    if len(events) == kwargs["limit"]:
        count += " (limit reached — more may match)"
    return _page(
        "nyc-events — browse",
        f"<h1>Browse events</h1>{_browse_form(params, facets)}{_preset_links(params)}"
        f"<p class='muted'>{count}</p>{table}",
    )


async def event_detail_page(request: Request) -> HTMLResponse:
    event_id = request.path_params["event_id"]
    now = datetime.now(UTC)
    try:
        with db.connect_events_ro(config.DB_PATH) as conn:
            ev = db.get_event_by_id(conn, event_id)
    except sqlite3.OperationalError as exc:
        if not _is_missing_db(exc):
            raise
        return _no_db_page()
    if ev is None:
        return _page(
            "nyc-events — not found",
            "<h1>Event not found</h1>"
            f"<p class='error'>No event with id {_esc(event_id)}.</p>",
            status=404,
        )
    map_url = _venue_map_url(ev.venue_name, ev.borough.value if ev.borough else None)
    safe_url = _safe_url(ev.url)
    raw = None
    if ev.raw_payload:
        try:
            raw = json.dumps(json.loads(ev.raw_payload), indent=2)
        except json.JSONDecodeError:
            raw = ev.raw_payload
    fields: list[tuple[str, str]] = [
        ("Source", _esc(_source_label(ev.source))),
        ("External id", _esc(ev.external_id)),
        ("Starts (NYC)", _esc(ev.start_dt.astimezone(NYC_TZ).strftime("%Y-%m-%d %H:%M"))),
        (
            "Ends (NYC)",
            _esc(ev.end_dt.astimezone(NYC_TZ).strftime("%Y-%m-%d %H:%M") if ev.end_dt else None),
        ),
        ("Venue", _esc(ev.venue_name)),
        ("Neighborhood", _esc(ev.neighborhood)),
        ("Borough", _esc(ev.borough.value if ev.borough else None)),
        ("Lat / lng", _esc(f"{ev.lat}, {ev.lng}" if ev.lat is not None else None)),
        ("Price", _esc(ev.price.value)),
        (
            "Ages",
            _esc(
                f"{ev.age_min if ev.age_min is not None else '?'}–"
                f"{ev.age_max if ev.age_max is not None else '?'}"
                if ev.age_min is not None or ev.age_max is not None
                else None
            ),
        ),
        ("Tags", _esc(", ".join(ev.tags) or None)),
        (
            "URL",
            f"<a href='{html.escape(safe_url, quote=True)}'"
            f" rel='noopener noreferrer'>{_esc(safe_url)}</a>"
            if safe_url
            else _esc(ev.url),  # non-http(s) scheme renders as text, not a link
        ),
        (
            "Map",
            f"<a href='{html.escape(map_url, quote=True)}'"
            f" rel='noopener noreferrer'>{_esc(map_url)}</a>"
            if map_url
            else _esc(None),
        ),
        ("Low confidence", _esc(ev.description is None and ev.url is None)),
        ("Possibly cancelled", _esc(_possibly_cancelled(ev.missing_since, now))),
        ("Description", f"<div>{_esc(ev.description)}</div>"),
    ]
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in fields)
    raw_block = (
        f"<details><summary>Raw upstream payload</summary><pre>{_esc(raw)}</pre></details>"
        if raw
        else ""
    )
    return _page(
        f"nyc-events — {ev.title}",
        f"<h1>{_esc(ev.title)}</h1><dl>{dl}</dl>{raw_block}"
        '<p><a href="/events">Back to browse</a></p>',
    )


def build_app() -> Starlette:
    """Assemble the dashboard app. GET routes only — adding any other method
    breaks the read-only contract (and a test). Never calls init_* : this
    process must not be able to create or migrate the DB."""
    return Starlette(
        routes=[
            Route("/", health_page, methods=["GET"]),
            Route("/events", events_page, methods=["GET"]),
            Route("/event/{event_id}", event_detail_page, methods=["GET"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


def main() -> None:
    # 0.0.0.0 inside the container; compose binds the host side to
    # 127.0.0.1:8766 and `tailscale serve` is the only path in.
    uvicorn.run(build_app(), host="0.0.0.0", port=config.DASHBOARD_PORT)


if __name__ == "__main__":
    main()
