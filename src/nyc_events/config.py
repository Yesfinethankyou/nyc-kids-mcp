"""Env-derived runtime settings, read once at import.

One home for every setting that used to be scattered across server/ingest/
enrich/seed_fake (`DB_PATH` alone was read in four places). Consumers do
attribute access at call time (`config.DB_PATH`, never
`from .config import DB_PATH`) so tests can monkeypatch attributes here and
every module sees the change.

Deliberately NOT here: the credentials. `MCP_AUTH_TOKEN` /
`MCP_CONSENT_PASSWORD` stay call-time `os.environ` reads in auth.py /
server.py — the master token should never sit in an importable module
attribute, and build_app's "refuse to start without a token" check must
happen at startup, not at import.
"""

from __future__ import annotations

import os

DB_PATH = os.environ.get("DB_PATH", "data/events.db")
# OAuth state in a separate SQLite file so wiping events.db during dev
# doesn't blow away access tokens claude.ai has cached.
OAUTH_DB_PATH = os.environ.get("OAUTH_DB_PATH", "data/oauth.db")

PORT = int(os.environ.get("PORT", "8765"))

# Port for the read-only tailnet dashboard (nyc_events.dashboard) — a
# separate process from the MCP server; exposed via `tailscale serve`
# (tailnet-only), never Funnel. See DASHBOARD-PLAN.md.
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8766"))

# Comma-separated list of source IPs (or CIDR ranges) whose X-Forwarded-*
# headers uvicorn should trust. Default: loopback only (the Tailscale Funnel
# tailscaled daemon forwards from 127.0.0.1 on the host). For Docker on
# Synology, set to include the bridge network, e.g. "127.0.0.1,172.16.0.0/12".
# Setting "*" (the previous default) trusts forwarded headers from any source,
# which lets anyone reaching uvicorn directly forge the public hostname in
# OAuth discovery JSON.
FORWARDED_ALLOW_IPS = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

# Default lifetime for OAuth-issued access tokens. Rotating MCP_AUTH_TOKEN
# does NOT invalidate already-issued access tokens (see README); this TTL
# bounds the window of an undetected token leak.
OAUTH_TOKEN_TTL_DAYS = int(os.environ.get("OAUTH_TOKEN_TTL_DAYS", "90"))

# Comma-separated allowlist for OAuth redirect_uri values. Each entry is
# matched by URL components (exact scheme + hostname, port if pinned, path
# prefix if present) — see auth._redirect_uri_allowed. Without this,
# /authorize will redirect the auth code to ANY URL, enabling a phishing flow
# that lures the user into typing the master token while the code lands at an
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
