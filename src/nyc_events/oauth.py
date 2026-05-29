"""Minimal OAuth 2.1 + PKCE provider for single-user MCP access.

claude.ai web's custom-connector flow requires OAuth discovery and won't accept
a static bearer header. This shim is the smallest thing that satisfies the
spec:

- Dynamic Client Registration accepts anything, returns a generated client_id.
- /authorize shows a one-field HTML consent page asking for the master token.
- /token issues a long-lived opaque access token, stored in SQLite.
- MCP_AUTH_TOKEN env var still works as a direct bearer (curl testing).

Auth codes live in-process (short-lived, single-use); access tokens live in
SQLite so they survive restarts.
"""

from __future__ import annotations

import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256

AUTH_CODE_TTL_SECONDS = 300


@dataclass
class AuthCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    expires_at: float
    scope: str | None = None


_pending: dict[str, AuthCode] = {}


def issue_auth_code(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str | None = None,
) -> str:
    code = secrets.token_urlsafe(32)
    _pending[code] = AuthCode(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method or "plain",
        expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
        scope=scope,
    )
    _gc()
    return code


def consume_auth_code(
    *, code: str, code_verifier: str, redirect_uri: str
) -> AuthCode | None:
    ac = _pending.pop(code, None)
    if ac is None or time.time() > ac.expires_at:
        return None
    if ac.redirect_uri != redirect_uri:
        return None
    if not _verify_pkce(ac.code_challenge, ac.code_challenge_method, code_verifier):
        return None
    return ac


def _verify_pkce(challenge: str, method: str, verifier: str) -> bool:
    if not verifier:
        return False
    if method == "S256":
        digest = sha256(verifier.encode("ascii")).digest()
        computed = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return secrets.compare_digest(computed, challenge)
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    return False


def _gc() -> None:
    now = time.time()
    for code in [c for c, ac in _pending.items() if ac.expires_at < now]:
        _pending.pop(code, None)


def new_client_id() -> str:
    return "claude-" + secrets.token_urlsafe(16)


def new_access_token() -> str:
    return secrets.token_urlsafe(48)
