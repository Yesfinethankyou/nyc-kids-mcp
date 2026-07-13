"""The auth surface: bearer middleware, rate limiter, and the OAuth 2.1 shim's
HTTP handlers (discovery, /register, /authorize, /token, consent page).

claude.ai web won't accept a static bearer for custom MCP connectors — it does
OAuth 2.1 + PKCE discovery. The endpoints below are the minimum shim needed;
see oauth.py for the code/PKCE helper logic. Direct curl with the master token
still works (the middleware accepts master token OR an OAuth-issued token).

This module is the "do not regress" security surface (see CLAUDE.md's HTTP
security baseline). The MCP tools live in tools.py; server.py composes the
two. Keep tool churn out of this file.

Single-process assumption: the rate-limiter buckets and OAuth token cache
below — plus the pending auth codes in oauth.py — are in-process dicts. The
app must run single-worker (uvicorn's default); with workers > 1 the OAuth
flow breaks non-deterministically (code issued on one worker, consumed on
another).
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import secrets
import time as _time
from collections import deque
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from . import config, db, oauth, users

# --- minimal in-process per-IP rate limiter ----------------------------------
# Bounds online guessing on /authorize POST (master-token), /token (code), and
# /register (resource exhaustion). Persists only in memory — a restart clears
# the counters, which is fine for personal-scale ops. relies on
# proxy_headers=True so request.client.host reflects X-Forwarded-For.

_rate_state: dict[tuple[str, str], deque[float]] = {}
# Short-lived in-memory cache of validated OAuth tokens (token → monotonic
# expiry). Avoids opening oauth.db on every MCP call. Tolerable revocation
# lag: up to _OAUTH_CACHE_TTL seconds before a deleted token stops working.
_oauth_token_cache: dict[str, float] = {}
_OAUTH_CACHE_TTL = 300  # seconds
_RATE_LIMITS: dict[str, tuple[int, int]] = {
    # endpoint -> (max_requests, window_seconds)
    "authorize_post": (5, 10),
    "token":          (5, 10),
    "register":       (20, 3600),
}
# Per-token limit on the authenticated MCP path (multi-user Phase B).
# Availability protection, not abuse defense: one person's runaway client
# must not starve the NAS for everyone else. Generous — a human-driven
# Claude conversation makes a handful of tool calls per minute.
_MCP_TOKEN_LIMIT: tuple[int, int] = (60, 60)  # (max_requests, window_seconds)

# Opportunistic global sweep (issue #77): per-key cleanup in _bucket_limited
# only runs when that same key is hit again, so a key that's never revisited
# (e.g. a scanner IP that probes /register once) keeps its bucket forever —
# unbounded growth of _rate_state over the process lifetime. Every
# _SWEEP_INTERVAL calls, drop any bucket whose newest timestamp is older than
# the largest configured window (3600s, matching "register"), so a stale
# bucket is reclaimed even without a repeat hit. Keeps the hot path O(1)
# amortized; no background task needed.
_SWEEP_INTERVAL = 1000
_SWEEP_MAX_AGE = 3600  # seconds; largest window across _RATE_LIMITS / _MCP_TOKEN_LIMIT
_calls_since_sweep = 0


def _sweep_rate_state(now: float) -> None:
    stale = [
        key for key, bucket in _rate_state.items()
        if not bucket or bucket[-1] < now - _SWEEP_MAX_AGE
    ]
    for key in stale:
        del _rate_state[key]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Cap on request bodies for the unauthenticated OAuth endpoints (issue #34).
# Their legitimate payloads are tiny (a DCR JSON stub, a consent form, a token
# exchange form); Starlette/uvicorn impose no default body limit, so without
# this a multi-hundred-MB POST to /register is buffered wholesale into memory.
_MAX_BODY_BYTES = 8192


async def _body_too_large(request: Request) -> bool:
    """True if the request body exceeds _MAX_BODY_BYTES.

    Content-Length is checked first as a cheap reject, but the real limit is
    enforced while draining the stream, so a chunked request that omits
    Content-Length can't bypass it. On success the body is cached on the
    request, and Starlette's .json()/.form() re-serve the cached bytes.
    """
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        return True
    if declared > _MAX_BODY_BYTES:
        return True
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            return True
    request._body = bytes(body)
    return False


def _payload_too_large() -> PlainTextResponse:
    return PlainTextResponse("payload too large", status_code=413)


def _bucket_limited(key: tuple[str, str], limit: int, window: int) -> Response | None:
    """Sliding-window check on one bucket: 429 if exhausted, else record the
    hit and return None. Shared by the per-IP limiter on the OAuth endpoints
    and the per-token limiter on the MCP path."""
    global _calls_since_sweep
    now = _time.time()
    _calls_since_sweep += 1
    if _calls_since_sweep >= _SWEEP_INTERVAL:
        _calls_since_sweep = 0
        _sweep_rate_state(now)
    bucket = _rate_state.get(key)
    if bucket is not None:
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if not bucket:
            del _rate_state[key]
            bucket = None
    if bucket is not None and len(bucket) >= limit:
        retry_after = max(1, int(window - (now - bucket[0])) + 1)
        return PlainTextResponse(
            "rate limit exceeded",
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    if bucket is None:
        bucket = deque()
        _rate_state[key] = bucket
    bucket.append(now)
    return None


def _rate_limit(request: Request, endpoint: str) -> Response | None:
    """Per-(client IP, endpoint) limit on the unauthenticated OAuth endpoints
    — bounds online guessing. Cheap; called at the top of each protected
    handler."""
    limit, window = _RATE_LIMITS[endpoint]
    return _bucket_limited((_client_ip(request), endpoint), limit, window)


def _token_rate_limit(presented: str) -> Response | None:
    """Per-token limit on authenticated MCP requests. Keyed by the token's
    hash so raw bearer material doesn't sit in another in-process structure
    longer than it must."""
    limit, window = _MCP_TOKEN_LIMIT
    key = ("tok:" + hashlib.sha256(presented.encode()).hexdigest(), "mcp")
    return _bucket_limited(key, limit, window)


class RedactAuthorizeQueryFilter(logging.Filter):
    """Scrub the /authorize query string from uvicorn access-log lines
    (multi-user Phase B). Single-user this was an accepted residual;
    with multiple users' consent redirects flowing through, the PKCE
    challenge / state / redirect params stay out of anything that might one
    day ship off-host. Wired onto the "uvicorn.access" logger in server.main.

    uvicorn's access records carry (client_addr, method, full_path,
    http_version, status_code) in record.args; only full_path is rewritten.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path = args[2]
            if isinstance(path, str) and path.startswith("/authorize?"):
                record.args = (*args[:2], "/authorize?[redacted]", *args[3:])
        return True


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
    for entry in config.OAUTH_REDIRECT_URI_ALLOWLIST:
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
            authorized = False
            if secrets.compare_digest(presented, self._master):
                authorized = True
            else:
                mono = _time.monotonic()
                if _oauth_token_cache.get(presented, 0.0) > mono:
                    authorized = True
                else:
                    with db.connect_oauth(self._oauth_db_path) as conn:
                        if db.is_valid_oauth_token(conn, presented):
                            _oauth_token_cache[presented] = mono + _OAUTH_CACHE_TTL
                            if len(_oauth_token_cache) > 200:
                                stale = [
                                    k for k, v in _oauth_token_cache.items()
                                    if v <= mono
                                ]
                                for k in stale:
                                    del _oauth_token_cache[k]
                            authorized = True
            if authorized:
                # Per-token availability limit (applies to the master bearer
                # too — a runaway curl loop is as capable of starving the NAS
                # as a runaway connector).
                limited = _token_rate_limit(presented)
                if limited is not None:
                    return limited
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
    if await _body_too_large(request):
        return _payload_too_large()
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
  <label for="token">Access code</label>
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
    if await _body_too_large(request):
        return _payload_too_large()
    form = await request.form()
    presented = form.get("token", "") or ""
    # Read at request time, not import time — the master token must never sit
    # in an importable module attribute (see config.py).
    master = os.environ.get("MCP_AUTH_TOKEN", "")
    consent_pw = os.environ.get("MCP_CONSENT_PASSWORD") or master
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
    # Two ways in: the operator's consent password (user_id stays None), or a
    # per-person invite code from the users table (user_id stamped through the
    # auth code onto the access token — see users.py).
    user_id: str | None = None
    authorized = bool(consent_pw and secrets.compare_digest(presented, consent_pw))
    if not authorized:
        with db.connect_oauth(config.OAUTH_DB_PATH) as conn:
            user_id = users.match_user(conn, presented)
        authorized = user_id is not None
    if not authorized:
        return _render_consent(params, error="Invalid access code.")

    code = oauth.issue_auth_code(
        client_id=params["client_id"],
        redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"],
        code_challenge_method=params["code_challenge_method"] or "plain",
        scope=params["scope"] or None,
        user_id=user_id,
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
    if await _body_too_large(request):
        return _payload_too_large()
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
    expires_at = datetime.now(UTC) + timedelta(days=config.OAUTH_TOKEN_TTL_DAYS)
    with db.connect_oauth(config.OAUTH_DB_PATH) as conn:
        db.store_oauth_token(
            conn, access_token, ac.client_id,
            scope=ac.scope, expires_at=expires_at, user_id=ac.user_id,
        )
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": config.OAUTH_TOKEN_TTL_DAYS * 24 * 3600,
            "scope": ac.scope or "",
        },
        headers={"Cache-Control": "no-store"},
    )
