"""Tests for the Checkpoint C security fix bundle.

Direct unit-style coverage on the helpers and DB layer. Full HTTP flow
testing is exercised manually via curl + claude.ai.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from starlette.requests import Request

from nyc_events import db
from nyc_events.auth import (
    _CONSENT_HEADERS,
    _MAX_BODY_BYTES,
    _RATE_LIMITS,
    _rate_limit,
    _rate_state,
    _redirect_uri_allowed,
    _render_consent,
    authorize_post,
    register,
    token_endpoint,
)

# ---- Fix #1: redirect_uri allowlist -----------------------------------------


def test_redirect_uri_allowlist_accepts_claude_ai_callback():
    assert _redirect_uri_allowed("https://claude.ai/api/mcp/auth_callback")
    assert _redirect_uri_allowed("https://claude.ai/api/mcp/auth_callback?x=1")


def test_redirect_uri_allowlist_accepts_localhost_variants():
    assert _redirect_uri_allowed("http://localhost:8080/callback")
    assert _redirect_uri_allowed("http://127.0.0.1:55555/cb")


def test_redirect_uri_allowlist_blocks_attacker_domains():
    assert not _redirect_uri_allowed("https://attacker.example.com/steal")
    assert not _redirect_uri_allowed("https://evil.tld/")
    assert not _redirect_uri_allowed("https://claude.ai.evil.tld/")  # no false-prefix
    assert not _redirect_uri_allowed("")


def test_redirect_uri_allowlist_blocks_hostname_prefix_bypass():
    # A bare-origin allowlist entry ("http://localhost") must not accept a
    # hostname that merely *starts with* the allowed host.
    assert not _redirect_uri_allowed("http://localhost.attacker.com/steal")
    assert not _redirect_uri_allowed("http://127.0.0.1.evil.tld/cb")
    assert not _redirect_uri_allowed("http://localhostx:8080/callback")


def test_redirect_uri_allowlist_requires_scheme_match():
    # The claude.ai entry is https; an http downgrade must not pass.
    assert not _redirect_uri_allowed("http://claude.ai/api/mcp/auth_callback")
    # localhost entries are http; https://localhost isn't allowlisted.
    assert not _redirect_uri_allowed("https://localhost:8080/callback")


def test_redirect_uri_allowlist_rejects_malformed_uris():
    assert not _redirect_uri_allowed("not a url")
    assert not _redirect_uri_allowed("http://localhost:notaport/cb")
    assert not _redirect_uri_allowed("//localhost/cb")  # scheme-relative


# ---- Fix #4: consent page security headers ----------------------------------


def test_consent_page_carries_security_headers():
    resp = _render_consent({
        "client_id": "x", "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        "code_challenge": "abc", "code_challenge_method": "S256",
        "state": "s", "scope": "mcp",
    })
    for name in ("X-Frame-Options", "X-Content-Type-Options",
                 "Referrer-Policy", "Content-Security-Policy"):
        assert name in resp.headers, f"missing {name}"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_consent_headers_constant_includes_csp_form_action():
    # form-action self stops a maliciously-styled form from posting elsewhere.
    assert "form-action 'self'" in _CONSENT_HEADERS["Content-Security-Policy"]


# ---- Fix #11: OAuth token expiry --------------------------------------------


@pytest.fixture
def oauth_conn(tmp_path):
    with db.connect_oauth(str(tmp_path / "oauth.db")) as c:
        yield c


def test_oauth_migration_adds_expires_at_column(tmp_path):
    import sqlite3
    p = str(tmp_path / "old.db")
    legacy = sqlite3.connect(p)
    legacy.executescript("""
        CREATE TABLE oauth_tokens (
            access_token TEXT PRIMARY KEY, client_id TEXT NOT NULL,
            scope TEXT, issued_at TEXT NOT NULL
        );
    """)
    legacy.commit()
    legacy.close()
    with db.connect_oauth(p) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)")}
    assert "expires_at" in cols


def test_token_with_future_expiry_is_valid(oauth_conn):
    db.store_oauth_token(
        oauth_conn, "tk-future", "client-x", scope="mcp",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    assert db.is_valid_oauth_token(oauth_conn, "tk-future")


def test_token_with_past_expiry_is_invalid(oauth_conn):
    db.store_oauth_token(
        oauth_conn, "tk-expired", "client-x", scope="mcp",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert not db.is_valid_oauth_token(oauth_conn, "tk-expired")


def test_legacy_null_expiry_still_valid(oauth_conn):
    # A token stored before this migration existed has expires_at=NULL.
    # is_valid_oauth_token should treat that as valid (manual DELETE = revoke).
    db.store_oauth_token(oauth_conn, "tk-legacy", "client-x", scope="mcp")
    assert db.is_valid_oauth_token(oauth_conn, "tk-legacy")


def test_unknown_token_is_invalid(oauth_conn):
    assert not db.is_valid_oauth_token(oauth_conn, "does-not-exist")


# ---- Fix #3: rate limiter ---------------------------------------------------


class _FakeRequest:
    def __init__(self, ip: str):
        class _C:
            host = ip
        self.client = _C()


def _reset_rate_state():
    _rate_state.clear()


def test_rate_limiter_allows_up_to_limit():
    _reset_rate_state()
    limit, _ = _RATE_LIMITS["authorize_post"]
    req = _FakeRequest("10.0.0.1")
    for _ in range(limit):
        assert _rate_limit(req, "authorize_post") is None


def test_rate_limiter_blocks_over_limit():
    _reset_rate_state()
    limit, _ = _RATE_LIMITS["authorize_post"]
    req = _FakeRequest("10.0.0.2")
    for _ in range(limit):
        _rate_limit(req, "authorize_post")
    blocked = _rate_limit(req, "authorize_post")
    assert blocked is not None
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_rate_limiter_is_per_ip():
    _reset_rate_state()
    limit, _ = _RATE_LIMITS["authorize_post"]
    a = _FakeRequest("10.0.0.10")
    b = _FakeRequest("10.0.0.11")
    for _ in range(limit):
        _rate_limit(a, "authorize_post")
    # a is exhausted, b should still be allowed
    assert _rate_limit(a, "authorize_post") is not None
    assert _rate_limit(b, "authorize_post") is None


def test_rate_limiter_is_per_endpoint():
    _reset_rate_state()
    req = _FakeRequest("10.0.0.20")
    auth_limit, _ = _RATE_LIMITS["authorize_post"]
    for _ in range(auth_limit):
        _rate_limit(req, "authorize_post")
    # /token should have its own bucket
    assert _rate_limit(req, "token") is None


# ---- Issue #34: body-size cap on unauthenticated OAuth endpoints -------------


def _http_request(
    path: str,
    *,
    method: str = "POST",
    chunks: list[bytes] | None = None,
    headers: dict[str, str] | None = None,
    ip: str = "10.99.0.1",
) -> Request:
    """Build a real Starlette Request whose body arrives via `chunks`.

    No Content-Length is synthesized — pass it in `headers` explicitly, so the
    chunked/streaming path (no declared length) is testable too.
    """
    raw_headers = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": (ip, 40000),
        "server": ("testserver", 80),
    }
    body_chunks = chunks if chunks is not None else [b""]
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": i < len(body_chunks) - 1,
        }
        for i, chunk in enumerate(body_chunks)
    ]
    it = iter(messages)

    async def receive():
        return next(it)

    return Request(scope, receive)


def test_register_rejects_oversized_declared_content_length():
    _reset_rate_state()
    # Cheap reject on the header alone — the body is never read.
    req = _http_request(
        "/register",
        chunks=[b"{}"],
        headers={"content-length": str(_MAX_BODY_BYTES + 1)},
        ip="10.99.0.2",
    )
    resp = asyncio.run(register(req))
    assert resp.status_code == 413


def test_register_rejects_oversized_streamed_body_without_content_length():
    _reset_rate_state()
    # A chunked upload omits Content-Length; the cap must bind on the stream.
    chunk = b"x" * 4096
    req = _http_request("/register", chunks=[chunk, chunk, chunk], ip="10.99.0.3")
    resp = asyncio.run(register(req))
    assert resp.status_code == 413


def test_register_within_limit_still_parses_body():
    _reset_rate_state()
    body = b'{"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}'
    req = _http_request(
        "/register",
        chunks=[body],
        headers={
            "content-length": str(len(body)),
            "content-type": "application/json",
        },
        ip="10.99.0.4",
    )
    resp = asyncio.run(register(req))
    assert resp.status_code == 201
    import json

    payload = json.loads(resp.body)
    # The parsed JSON body made it through the size guard's cached stream.
    assert payload["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    assert payload["client_id"]


def test_authorize_post_rejects_oversized_body():
    _reset_rate_state()
    req = _http_request(
        "/authorize",
        chunks=[b"t" * (_MAX_BODY_BYTES + 1)],
        headers={"content-type": "application/x-www-form-urlencoded"},
        ip="10.99.0.5",
    )
    resp = asyncio.run(authorize_post(req))
    assert resp.status_code == 413


def test_token_rejects_oversized_body():
    _reset_rate_state()
    req = _http_request(
        "/token",
        chunks=[b"g" * (_MAX_BODY_BYTES + 1)],
        headers={"content-type": "application/x-www-form-urlencoded"},
        ip="10.99.0.6",
    )
    resp = asyncio.run(token_endpoint(req))
    assert resp.status_code == 413


def test_token_undersized_body_still_reaches_grant_validation():
    _reset_rate_state()
    body = b"grant_type=bogus"
    req = _http_request(
        "/token",
        chunks=[body],
        headers={
            "content-length": str(len(body)),
            "content-type": "application/x-www-form-urlencoded",
        },
        ip="10.99.0.7",
    )
    resp = asyncio.run(token_endpoint(req))
    # Past the size guard, the form parsed, and the normal OAuth error path ran.
    assert resp.status_code == 400
