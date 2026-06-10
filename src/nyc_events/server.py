"""FastMCP streamable-HTTP server + OAuth shim + bearer ASGI middleware.

claude.ai web won't accept a static bearer for custom MCP connectors — it does
OAuth 2.1 + PKCE discovery. The OAuth endpoints below are the minimum shim
needed; see oauth.py for the helper logic. Direct curl with the master token
still works (the middleware accepts master token OR an OAuth-issued token).
"""

from __future__ import annotations

import html
import os
import secrets
import time as _time
from collections import deque
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route

from . import db, oauth
from .models import Event

NYC_TZ = ZoneInfo("America/New_York")
UTC = UTC
DB_PATH = os.environ.get("DB_PATH", "data/events.db")
# OAuth state in a separate SQLite file so wiping events.db during dev
# doesn't blow away access tokens claude.ai has cached.
OAUTH_DB_PATH = os.environ.get("OAUTH_DB_PATH", "data/oauth.db")
PORT = int(os.environ.get("PORT", "8765"))
# Comma-separated list of source IPs (or CIDR ranges) whose X-Forwarded-*
# headers uvicorn should trust. Default: loopback only (the Tailscale Funnel
# tailscaled daemon forwards from 127.0.0.1 on the host). For Docker on
# Synology, set to include the bridge network, e.g. "127.0.0.1,172.16.0.0/12".
# Setting "*" (the previous default) trusts forwarded headers from any source,
# which lets anyone reaching uvicorn directly forge the public hostname in
# OAuth discovery JSON.
FORWARDED_ALLOW_IPS = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

# Comma-separated allowlist for OAuth redirect_uri values. Each entry is
# matched by URL components (exact scheme + hostname, port if pinned, path
# prefix if present) — see _redirect_uri_allowed. Without this, /authorize
# will redirect the auth code to ANY URL, enabling a phishing flow that
# lures the user into typing the master token while the code lands at an
# attacker-controlled URL. Defaults cover claude.ai and local
# mcp-inspector clients.
_DEFAULT_REDIRECT_PREFIXES = (
    "https://claude.ai/api/mcp/auth_callback",
    "http://localhost",
    "http://127.0.0.1",
)
OAUTH_REDIRECT_URI_ALLOWLIST = tuple(
    p.strip() for p in os.environ.get(
        "OAUTH_REDIRECT_URI_ALLOWLIST",
        ",".join(_DEFAULT_REDIRECT_PREFIXES),
    ).split(",") if p.strip()
)

# Default lifetime for OAuth-issued access tokens. Rotating MCP_AUTH_TOKEN
# does NOT invalidate already-issued access tokens (see README); this TTL
# bounds the window of an undetected token leak.
OAUTH_TOKEN_TTL_DAYS = int(os.environ.get("OAUTH_TOKEN_TTL_DAYS", "90"))


# --- minimal in-process per-IP rate limiter ----------------------------------
# Bounds online guessing on /authorize POST (master-token), /token (code), and
# /register (resource exhaustion). Persists only in memory — a restart clears
# the counters, which is fine for personal-scale ops. relies on
# proxy_headers=True so request.client.host reflects X-Forwarded-For.

_rate_state: dict[tuple[str, str], deque[float]] = {}
_RATE_LIMITS: dict[str, tuple[int, int]] = {
    # endpoint -> (max_requests, window_seconds)
    "authorize_post": (5, 10),
    "token":          (5, 10),
    "register":       (20, 3600),
}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, endpoint: str) -> Response | None:
    """Return a 429 response if the (IP, endpoint) bucket is exhausted, else
    record this hit and return None. Cheap; called at the top of each
    protected handler. Auth-success path is never throttled — only the
    failure-prone surface where guessing happens."""
    limit, window = _RATE_LIMITS[endpoint]
    ip = _client_ip(request)
    key = (ip, endpoint)
    bucket = _rate_state.setdefault(key, deque())
    now = _time.time()
    while bucket and bucket[0] < now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(window - (now - bucket[0])) + 1)
        return PlainTextResponse(
            "rate limit exceeded",
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)
    return None


def _redirect_uri_allowed(uri: str) -> bool:
    """Match against the allowlist by URL components, not string prefix.

    Naive startswith() on a bare-origin entry like "http://localhost" would
    also accept "http://localhost.attacker.com/steal". Instead: scheme and
    hostname must match the entry exactly; port must match if the entry
    pins one; path must prefix-match if the entry has one (so any port and
    path are fine for the localhost entries, while the claude.ai entry
    stays pinned to its callback path).
    """
    try:
        parsed = urlparse(uri)
        parsed_port = parsed.port  # property access can raise on bad ports
    except ValueError:
        return False
    if not parsed.scheme or not parsed.hostname:
        return False
    for entry in OAUTH_REDIRECT_URI_ALLOWLIST:
        allowed = urlparse(entry)
        if parsed.scheme != allowed.scheme:
            continue
        if parsed.hostname != allowed.hostname:
            continue
        if allowed.port is not None and parsed_port != allowed.port:
            continue
        if allowed.path and not parsed.path.startswith(allowed.path):
            continue
        return True
    return False

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
# before tools surface possibly_cancelled. 30h ≈ two consecutive nightly
# runs: a one-night blip stamps rows, the next night clears them, and no
# user ever sees the flag.
_MISSING_GRACE_HOURS = 30


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
        "borough": ev.borough.value if ev.borough else None,
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
    everything in the summary plus end_local, neighborhood, age range,
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


@mcp.tool()
def search_events(
    query: str | None = None,
    borough: str | None = None,
    age: int | None = None,
    free_only: bool = False,
    days_ahead: int = 14,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search NYC family-friendly events with optional filters.

    Use this for free-text questions like "outdoor music in Brooklyn" or
    "museum activities for a 4-year-old". Combine with the filter args to
    narrow down. Results are ordered by start time.

    Each result has a `low_confidence: bool` flag — true means the row came
    from a permit-style source (no description, no URL) and may not be a
    public-facing event. Surface that uncertainty to the user instead of
    assuming the event is attendable.

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
        age: kid's age in years. Returns events whose [age_min, age_max]
            window includes this age, plus events without a declared range.
        free_only: if True, only events explicitly flagged free.
        days_ahead: window starts now and ends N days from today (default 14).
        limit: max events to return (default 10). Use
            get_event_detail(event_id) to drill into a specific result.
    """
    now = datetime.now(NYC_TZ)
    until = now + timedelta(days=days_ahead)
    with db.connect_events(DB_PATH) as conn:
        events = db.search(
            conn,
            query=query,
            borough=_normalize_borough(borough),
            age=age,
            free_only=free_only,
            start_after=now.astimezone(UTC),
            start_before=until.astimezone(UTC),
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
        limit: max events to return (default 10). Use
            get_event_detail(event_id) to drill into a specific result.
    """
    window_start, sunday_end = _weekend_window(datetime.now(NYC_TZ))
    with db.connect_events(DB_PATH) as conn:
        events = db.search(
            conn,
            borough=_normalize_borough(borough),
            age=age,
            free_only=free_only,
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
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Events on a specific local NYC date.

    Args:
        date: YYYY-MM-DD (interpreted as America/New_York local).
        borough: Manhattan, Brooklyn, Queens, Bronx, or Staten Island.
        age: kid's age in years.
        free_only: if True, only events explicitly flagged free.
        limit: max events to return (default 10). Use
            get_event_detail(event_id) to drill into a specific result.
    """
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}") from exc
    day_start = datetime.combine(d, time(0, 0), NYC_TZ)
    day_end = datetime.combine(d, time(23, 59, 59), NYC_TZ)
    with db.connect_events(DB_PATH) as conn:
        events = db.search(
            conn,
            borough=_normalize_borough(borough),
            age=age,
            free_only=free_only,
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
    specific event and you need everything: full description, end_local,
    neighborhood, lat/lng, age range, and the upstream external_id.

    Returns None if the event_id isn't found. For the original upstream
    payload, see get_event_raw instead.
    """
    with db.connect_events(DB_PATH) as conn:
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
    import json as _json
    with db.connect_events(DB_PATH) as conn:
        ev = db.get_event_by_id(conn, event_id)
    if ev is None or ev.raw_payload is None:
        return None
    try:
        return _json.loads(ev.raw_payload)
    except _json.JSONDecodeError:
        return None


@mcp.tool()
def list_sources() -> list[dict[str, Any]]:
    """List ingested event sources with counts and freshness.

    Returns one row per source with event_count, earliest_event, latest_event,
    and last_seen. Useful for diagnosing stale or empty sources.
    """
    with db.connect_events(DB_PATH) as conn:
        return db.list_sources(conn)


# Paths the bearer middleware lets through unauthenticated.
_PUBLIC_PATHS = {
    "/healthz",
    "/healthz/",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server",
    "/authorize",
    "/token",
    "/register",
}


def _base_url(request: Request) -> str:
    # Behind Funnel / reverse proxies, base_url reflects forwarded host+scheme
    # when uvicorn is run with proxy_headers (the default).
    return str(request.base_url).rstrip("/")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Accepts either the master MCP_AUTH_TOKEN or a stored OAuth access token."""

    def __init__(self, app, token: str, oauth_db_path: str):
        super().__init__(app)
        self._master = token
        self._oauth_db_path = oauth_db_path

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        scheme, _, presented = auth.partition(" ")
        if scheme.lower() == "bearer" and presented:
            if secrets.compare_digest(presented, self._master):
                return await call_next(request)
            with db.connect_oauth(self._oauth_db_path) as conn:
                if db.is_valid_oauth_token(conn, presented):
                    return await call_next(request)

        meta = f"{_base_url(request)}/.well-known/oauth-protected-resource"

        # claude.ai's web UI does a browser-side fetch(url) to verify the
        # connector URL is reachable BEFORE submitting the form. The browser
        # doesn't follow WWW-Authenticate discovery, so a 401 makes the UI
        # report "URL is bad" and the server-side OAuth dance never starts.
        # Respond 200 with a self-describing JSON to that probe (GET / with
        # no MCP session id and no bearer) so the UI accepts the URL.
        # claude.ai's backend then negotiates OAuth via POST as before.
        if (
            request.method == "GET"
            and request.url.path in ("/", "")
            and not request.headers.get("mcp-session-id")
        ):
            # Minimum payload that satisfies claude.ai's browser-side URL
            # reachability check. Intentionally does NOT name the project,
            # protocol family, or transport — those would broaden the
            # blackbox surface for unauthenticated probers.
            return JSONResponse(
                {
                    "authorization_required": True,
                    "resource_metadata": meta,
                },
                headers={
                    "WWW-Authenticate": (
                        f'Bearer realm="nyc-events", resource_metadata="{meta}"'
                    )
                },
            )

        return PlainTextResponse(
            "unauthorized",
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    f'Bearer realm="nyc-events", resource_metadata="{meta}"'
                )
            },
        )


async def healthz(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


# ---- OAuth discovery + endpoints --------------------------------------------


async def protected_resource_metadata(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse(
        {
            "resource": f"{base}/",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp"],
        }
    )


async def authorization_server_metadata(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp"],
        }
    )


async def register(request: Request) -> Response:
    limited = _rate_limit(request, "register")
    if limited is not None:
        return limited
    # RFC 7591 says POST-only. We accept GET too as a soft guard against the
    # same http→https-redirect-downgrade pitfall described on /token: if the
    # advertised registration_endpoint is http://… and Funnel 302s to https,
    # httpx silently rewrites POST→GET and drops the body. DCR is already a
    # security no-op (any client_id works), so widening the method is fine.
    body: dict = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
    client_id = oauth.new_client_id()
    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(_time.time()),
            "redirect_uris": body.get("redirect_uris", []),
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
        status_code=201,
    )


_CONSENT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Approve NYC Kids MCP</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 480px;
         margin: 4rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ font-size: 1.2rem; }}
  p {{ color: #555; }}
  code {{ background:#eef; padding:0 .25rem; border-radius:3px; }}
  label {{ display:block; margin: 1rem 0 .25rem; font-weight:600; }}
  input[type=password] {{ width:100%; padding:.5rem; font-size:1rem;
         border:1px solid #aaa; border-radius:4px; box-sizing:border-box; }}
  button {{ margin-top:1rem; padding:.6rem 1.2rem; font-size:1rem;
         background:#222; color:#fff; border:none; border-radius:4px;
         cursor:pointer; }}
  .err {{ color:#c00; }}
</style>
</head><body>
<h1>Approve NYC Kids MCP access?</h1>
<p>A client wants to connect: <code>{client_id}</code><br>
Redirect after approval: <code>{redirect_uri}</code></p>
{error}
<form method="POST" action="/authorize">
  <input type="hidden" name="client_id" value="{client_id}">
  <input type="hidden" name="redirect_uri" value="{redirect_uri}">
  <input type="hidden" name="code_challenge" value="{code_challenge}">
  <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
  <input type="hidden" name="state" value="{state}">
  <input type="hidden" name="scope" value="{scope}">
  <label for="token">Master token</label>
  <input id="token" type="password" name="token" required autofocus>
  <button type="submit">Approve</button>
</form>
</body></html>"""


_CONSENT_HEADERS = {
    # Defense in depth on the only HTML response the server serves. Consent
    # page is the credential-typing surface, so harden it against framing
    # (clickjacking), MIME sniffing, third-party resource loads, and
    # referrer leakage of the auth code in subsequent navigation.
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


def _render_consent(params: dict[str, str], error: str = "") -> HTMLResponse:
    safe = {k: html.escape(v or "") for k, v in params.items()}
    body = _CONSENT_HTML.format(
        client_id=safe.get("client_id", ""),
        redirect_uri=safe.get("redirect_uri", ""),
        code_challenge=safe.get("code_challenge", ""),
        code_challenge_method=safe.get("code_challenge_method", "plain"),
        state=safe.get("state", ""),
        scope=safe.get("scope", ""),
        error=f'<p class="err">{html.escape(error)}</p>' if error else "",
    )
    return HTMLResponse(body, headers=_CONSENT_HEADERS)


async def authorize_get(request: Request) -> Response:
    qp = request.query_params
    if not qp.get("client_id") or not qp.get("redirect_uri"):
        return PlainTextResponse("missing client_id or redirect_uri", status_code=400)
    if not _redirect_uri_allowed(qp.get("redirect_uri", "")):
        # Refuse to even show the consent page for a non-allowlisted URI;
        # this is the open-redirect / phishing mitigation.
        return PlainTextResponse(
            "redirect_uri not in allowlist", status_code=400
        )
    return _render_consent(dict(qp))


async def authorize_post(request: Request) -> Response:
    limited = _rate_limit(request, "authorize_post")
    if limited is not None:
        return limited
    form = await request.form()
    presented = form.get("token", "") or ""
    master = os.environ.get("MCP_AUTH_TOKEN", "")
    params = {k: form.get(k, "") for k in (
        "client_id", "redirect_uri", "code_challenge",
        "code_challenge_method", "state", "scope",
    )}
    # Re-check on POST: hidden form fields are user-controllable, so an
    # attacker who only opens /authorize GET on an allowlisted URI cannot
    # then POST with a swapped redirect_uri to bypass the GET-side check.
    if not _redirect_uri_allowed(params.get("redirect_uri", "")):
        return PlainTextResponse(
            "redirect_uri not in allowlist", status_code=400
        )
    if not (master and secrets.compare_digest(presented, master)):
        return _render_consent(params, error="Invalid token.")

    code = oauth.issue_auth_code(
        client_id=params["client_id"],
        redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"],
        code_challenge_method=params["code_challenge_method"] or "plain",
        scope=params["scope"] or None,
    )
    sep = "&" if "?" in params["redirect_uri"] else "?"
    location = f'{params["redirect_uri"]}{sep}code={code}'
    if params["state"]:
        location += f'&state={params["state"]}'
    return RedirectResponse(location, status_code=302)


async def token_endpoint(request: Request) -> Response:
    limited = _rate_limit(request, "token")
    if limited is not None:
        return limited
    # Accept GET in addition to POST: when the OAuth metadata accidentally
    # advertises an http:// token endpoint (e.g. FORWARDED_ALLOW_IPS not set
    # for a Docker bridge), Tailscale Funnel 302s the POST to https, and
    # httpx auto-follows by downgrading POST→GET and dropping the body.
    # Accepting GET means we 400 with a useful error instead of silently
    # 404-ing through the MCP catch-all. The real fix is upstream config.
    if request.method == "POST":
        params = await request.form()
    else:
        params = request.query_params
    if params.get("grant_type") != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    ac = oauth.consume_auth_code(
        code=params.get("code", "") or "",
        code_verifier=params.get("code_verifier", "") or "",
        redirect_uri=params.get("redirect_uri", "") or "",
    )
    if ac is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    access_token = oauth.new_access_token()
    expires_at = datetime.now(UTC) + timedelta(days=OAUTH_TOKEN_TTL_DAYS)
    with db.connect_oauth(OAUTH_DB_PATH) as conn:
        db.store_oauth_token(
            conn, access_token, ac.client_id,
            scope=ac.scope, expires_at=expires_at,
        )
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": OAUTH_TOKEN_TTL_DAYS * 24 * 3600,
            "scope": ac.scope or "",
        },
        headers={"Cache-Control": "no-store"},
    )


def build_app() -> Starlette:
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN env var is required")
    # streamable_http_app() lazily materializes mcp.session_manager. The inner
    # app's lifespan is what starts the session manager's task group; we have
    # to forward it through our outer Starlette or every request 500s with
    # "Task group is not initialized."
    mcp_app = mcp.streamable_http_app()
    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route(
                "/.well-known/oauth-protected-resource",
                protected_resource_metadata,
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                protected_resource_metadata,
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                authorization_server_metadata,
            ),
            Route("/register", register, methods=["GET", "POST"]),
            Route("/authorize", authorize_get, methods=["GET"]),
            Route("/authorize", authorize_post, methods=["POST"]),
            Route("/token", token_endpoint, methods=["GET", "POST"]),
            Mount("/", app=mcp_app),
        ],
        middleware=[
            Middleware(BearerAuthMiddleware, token=token, oauth_db_path=OAUTH_DB_PATH),
        ],
        lifespan=lambda _app: mcp.session_manager.run(),
    )


def main() -> None:
    app = build_app()
    # forwarded_allow_ips="*" lets request.base_url reflect the public
    # https://<host>/... that Tailscale Funnel forwards in X-Forwarded-* —
    # otherwise the OAuth discovery JSON would advertise http://0.0.0.0:8765/.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        proxy_headers=True,
        forwarded_allow_ips=FORWARDED_ALLOW_IPS,
    )


if __name__ == "__main__":
    main()
