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
    path = str(tmp_path / "oauth.db")
    db.init_oauth(path)
    with db.connect_oauth(path) as c:
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
    db.init_oauth(p)  # schema DDL + migrations now live in init, not connect
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


# ---- Phase A multi-user: invite codes, attribution, revocation ---------------
# (MULTI-USER-PLAN.md — per-person credentials on the consent flow.)

from urllib.parse import urlencode  # noqa: E402

from nyc_events import config, oauth, users  # noqa: E402


def test_oauth_migration_adds_user_id_column(tmp_path):
    import sqlite3
    p = str(tmp_path / "old.db")
    legacy = sqlite3.connect(p)
    legacy.executescript("""
        CREATE TABLE oauth_tokens (
            access_token TEXT PRIMARY KEY, client_id TEXT NOT NULL,
            scope TEXT, issued_at TEXT NOT NULL, expires_at TEXT
        );
    """)
    legacy.commit()
    legacy.close()
    db.init_oauth(p)
    with db.connect_oauth(p) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)")}
        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "user_id" in cols
    assert "users" in tables


def test_passcode_hash_is_salted_and_verifies():
    code = users.generate_passcode()
    h1 = users.hash_passcode(code)
    h2 = users.hash_passcode(code)
    assert h1 != h2  # fresh salt per call
    assert users.verify_passcode(code, h1)
    assert users.verify_passcode(code, h2)
    assert not users.verify_passcode("wrong-code", h1)
    assert not users.verify_passcode(code, "not-a-valid-hash")
    assert not users.verify_passcode(code, "")


def test_match_user_finds_the_right_user(oauth_conn):
    code_a = users.generate_passcode()
    code_b = users.generate_passcode()
    db.create_user(oauth_conn, user_id="user-a", name="alice",
                   passcode_hash=users.hash_passcode(code_a))
    db.create_user(oauth_conn, user_id="user-b", name="bob",
                   passcode_hash=users.hash_passcode(code_b))
    assert users.match_user(oauth_conn, code_a) == "user-a"
    assert users.match_user(oauth_conn, code_b) == "user-b"
    assert users.match_user(oauth_conn, "no-such-code") is None
    assert users.match_user(oauth_conn, "") is None


def test_revoke_disables_code_and_deletes_only_their_tokens(oauth_conn):
    code = users.generate_passcode()
    db.create_user(oauth_conn, user_id="user-r", name="revokee",
                   passcode_hash=users.hash_passcode(code))
    db.store_oauth_token(oauth_conn, "tk-theirs", "client-1", user_id="user-r")
    db.store_oauth_token(oauth_conn, "tk-operator", "client-2")  # NULL user_id
    assert users.match_user(oauth_conn, code) == "user-r"

    deleted = db.revoke_user(oauth_conn, "user-r")
    assert deleted == 1
    assert users.match_user(oauth_conn, code) is None
    assert not db.is_valid_oauth_token(oauth_conn, "tk-theirs")
    # The operator's unattributed token is untouched.
    assert db.is_valid_oauth_token(oauth_conn, "tk-operator")
    # Tombstone, not delete — attribution history survives.
    row = db.get_user_by_name(oauth_conn, "revokee")
    assert row is not None and row["revoked_at"] is not None


def test_duplicate_user_name_is_rejected(oauth_conn):
    import sqlite3
    db.create_user(oauth_conn, user_id="user-1", name="dup",
                   passcode_hash=users.hash_passcode("x"))
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user(oauth_conn, user_id="user-2", name="dup",
                       passcode_hash=users.hash_passcode("y"))


def test_auth_code_carries_user_id_through_consume():
    verifier = "some-plain-verifier"
    code = oauth.issue_auth_code(
        client_id="c1",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        code_challenge=verifier,
        code_challenge_method="plain",
        scope="mcp",
        user_id="user-42",
    )
    ac = oauth.consume_auth_code(
        code=code, code_verifier=verifier,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
    )
    assert ac is not None
    assert ac.user_id == "user-42"


def test_store_oauth_token_persists_user_id(oauth_conn):
    db.store_oauth_token(oauth_conn, "tk-attr", "client-x", user_id="user-9")
    row = oauth_conn.execute(
        "SELECT user_id FROM oauth_tokens WHERE access_token = ?",
        (db.hash_access_token("tk-attr"),),
    ).fetchone()
    assert row["user_id"] == "user-9"


def _consent_form_request(token_value: str, ip: str) -> Request:
    body = urlencode({
        "client_id": "client-web",
        "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        "code_challenge": "chal",
        "code_challenge_method": "plain",
        "state": "st",
        "scope": "mcp",
        "token": token_value,
    }).encode()
    return _http_request(
        "/authorize",
        chunks=[body],
        headers={
            "content-length": str(len(body)),
            "content-type": "application/x-www-form-urlencoded",
        },
        ip=ip,
    )


@pytest.fixture
def user_oauth_db(tmp_path, monkeypatch):
    """Point config.OAUTH_DB_PATH at a fresh DB holding one invite-code user."""
    path = str(tmp_path / "oauth.db")
    db.init_oauth(path)
    monkeypatch.setattr(config, "OAUTH_DB_PATH", path)
    monkeypatch.setenv("MCP_AUTH_TOKEN", "the-master-token")
    monkeypatch.delenv("MCP_CONSENT_PASSWORD", raising=False)
    code = users.generate_passcode()
    with db.connect_oauth(path) as conn:
        db.create_user(conn, user_id="user-f", name="friend",
                       passcode_hash=users.hash_passcode(code))
    return path, code


def test_authorize_post_accepts_user_invite_code(user_oauth_db):
    _reset_rate_state()
    _, code = user_oauth_db
    resp = asyncio.run(authorize_post(_consent_form_request(code, "10.77.0.1")))
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(
        "https://claude.ai/api/mcp/auth_callback?code="
    )


def test_authorize_post_still_accepts_operator_password(user_oauth_db):
    _reset_rate_state()
    resp = asyncio.run(
        authorize_post(_consent_form_request("the-master-token", "10.77.0.2"))
    )
    assert resp.status_code == 302


def test_authorize_post_rejects_unknown_code(user_oauth_db):
    _reset_rate_state()
    resp = asyncio.run(
        authorize_post(_consent_form_request("not-a-real-code", "10.77.0.3"))
    )
    # Consent page re-rendered with an error, no redirect.
    assert resp.status_code == 200
    assert b"Invalid access code" in resp.body


def test_authorize_post_rejects_revoked_users_code(user_oauth_db):
    _reset_rate_state()
    path, code = user_oauth_db
    with db.connect_oauth(path) as conn:
        db.revoke_user(conn, "user-f")
    resp = asyncio.run(authorize_post(_consent_form_request(code, "10.77.0.4")))
    assert resp.status_code == 200
    assert b"Invalid access code" in resp.body


def test_user_token_flow_stamps_attribution(user_oauth_db):
    _reset_rate_state()
    path, code = user_oauth_db
    resp = asyncio.run(authorize_post(_consent_form_request(code, "10.77.0.5")))
    assert resp.status_code == 302
    auth_code = resp.headers["location"].split("code=")[1].split("&")[0]
    body = urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "code_verifier": "chal",
        "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
    }).encode()
    token_req = _http_request(
        "/token",
        chunks=[body],
        headers={
            "content-length": str(len(body)),
            "content-type": "application/x-www-form-urlencoded",
        },
        ip="10.77.0.6",
    )
    token_resp = asyncio.run(token_endpoint(token_req))
    assert token_resp.status_code == 200
    import json
    access_token = json.loads(token_resp.body)["access_token"]
    with db.connect_oauth(path) as conn:
        row = conn.execute(
            "SELECT user_id FROM oauth_tokens WHERE access_token = ?",
            (db.hash_access_token(access_token),),
        ).fetchone()
    assert row["user_id"] == "user-f"


def test_users_cli_add_list_revoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "OAUTH_DB_PATH", str(tmp_path / "cli.db"))
    assert users.main(["add", "carol"]) == 0
    out = capsys.readouterr().out
    # The invite code is the indented line; it must verify against the stored hash.
    code = next(
        line.strip() for line in out.splitlines() if line.startswith("    ")
    )
    with db.connect_oauth(config.OAUTH_DB_PATH) as conn:
        assert users.match_user(conn, code) is not None
    assert users.main(["add", "carol"]) == 1  # duplicate name
    capsys.readouterr()
    assert users.main(["list"]) == 0
    assert "carol" in capsys.readouterr().out
    assert users.main(["revoke", "carol"]) == 0
    with db.connect_oauth(config.OAUTH_DB_PATH) as conn:
        assert users.match_user(conn, code) is None
    assert users.main(["revoke", "nobody"]) == 1


# ---- Phase B multi-user hardening ---------------------------------------------
# (MULTI-USER-PLAN.md — tokens hashed at rest, per-token MCP rate limit,
#  /authorize query strings out of the access log.)

import logging  # noqa: E402

from nyc_events.auth import (  # noqa: E402
    _MCP_TOKEN_LIMIT,
    RedactAuthorizeQueryFilter,
    _token_rate_limit,
)


def test_tokens_are_stored_hashed_at_rest(oauth_conn):
    db.store_oauth_token(oauth_conn, "tk-plain-secret", "client-x", scope="mcp")
    stored = [
        r["access_token"]
        for r in oauth_conn.execute("SELECT access_token FROM oauth_tokens")
    ]
    assert "tk-plain-secret" not in stored
    assert db.hash_access_token("tk-plain-secret") in stored
    assert all(t.startswith("sha256:") for t in stored)
    # The plaintext as presented on the wire still validates.
    assert db.is_valid_oauth_token(oauth_conn, "tk-plain-secret")
    # Presenting the stored hash itself must NOT validate (it gets re-hashed).
    assert not db.is_valid_oauth_token(
        oauth_conn, db.hash_access_token("tk-plain-secret")
    )


def test_migration_hashes_legacy_plaintext_tokens(tmp_path):
    import sqlite3
    p = str(tmp_path / "plain.db")
    legacy = sqlite3.connect(p)
    legacy.executescript("""
        CREATE TABLE oauth_tokens (
            access_token TEXT PRIMARY KEY, client_id TEXT NOT NULL,
            scope TEXT, issued_at TEXT NOT NULL, expires_at TEXT, user_id TEXT
        );
        INSERT INTO oauth_tokens VALUES
            ('legacy-plaintext-token', 'client-old', 'mcp',
             '2026-01-01T00:00:00+00:00', NULL, NULL);
    """)
    legacy.commit()
    legacy.close()
    db.init_oauth(p)
    db.init_oauth(p)  # idempotent — second run must not double-hash
    with db.connect_oauth(p) as conn:
        stored = conn.execute(
            "SELECT access_token FROM oauth_tokens"
        ).fetchone()["access_token"]
        assert stored == db.hash_access_token("legacy-plaintext-token")
        # The client's cached plaintext bearer keeps working post-migration.
        assert db.is_valid_oauth_token(conn, "legacy-plaintext-token")


def test_token_rate_limit_blocks_over_limit():
    _reset_rate_state()
    limit, _ = _MCP_TOKEN_LIMIT
    for _ in range(limit):
        assert _token_rate_limit("bearer-abc") is None
    blocked = _token_rate_limit("bearer-abc")
    assert blocked is not None
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_token_rate_limit_is_per_token():
    _reset_rate_state()
    limit, _ = _MCP_TOKEN_LIMIT
    for _ in range(limit):
        _token_rate_limit("bearer-one")
    assert _token_rate_limit("bearer-one") is not None
    # A different bearer has its own bucket.
    assert _token_rate_limit("bearer-two") is None


def test_token_rate_limit_does_not_store_raw_token():
    _reset_rate_state()
    _token_rate_limit("super-secret-bearer")
    for key, _endpoint in _rate_state:
        assert "super-secret-bearer" not in key


def _access_log_record(path: str) -> logging.LogRecord:
    # Mirrors uvicorn's access-log record shape: %s args =
    # (client_addr, method, full_path, http_version, status_code).
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname="", lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("1.2.3.4:5", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_redact_filter_scrubs_authorize_query_string():
    rec = _access_log_record(
        "/authorize?client_id=c&redirect_uri=https%3A%2F%2Fclaude.ai%2F"
        "&code_challenge=SECRETCHAL&state=SECRETSTATE"
    )
    assert RedactAuthorizeQueryFilter().filter(rec) is True
    rendered = rec.getMessage()
    assert "SECRETCHAL" not in rendered
    assert "SECRETSTATE" not in rendered
    assert "/authorize?[redacted]" in rendered


def test_redact_filter_leaves_other_paths_alone():
    rec = _access_log_record("/token")
    RedactAuthorizeQueryFilter().filter(rec)
    assert rec.args[2] == "/token"
    # Odd-shaped records (non-tuple args) pass through untouched.
    odd = logging.LogRecord(
        name="uvicorn.error", level=logging.INFO, pathname="", lineno=0,
        msg="plain message", args=None, exc_info=None,
    )
    assert RedactAuthorizeQueryFilter().filter(odd) is True
