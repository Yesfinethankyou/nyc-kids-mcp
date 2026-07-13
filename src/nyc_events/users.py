"""Per-person invite codes + the admin CLI.

Each trusted user gets a generated high-entropy invite code they paste on the
OAuth consent page instead of the shared consent password. Only a salted hash
is stored; the plaintext is printed exactly once by `add`. The consent flow
(auth.authorize_post) calls match_user() to find whose code was presented,
and that user_id is stamped onto the issued access token for attribution and
per-person revocation.

Admin surface is this CLI — there is deliberately no web UI:

    python -m nyc_events.users add <name>     # create user, print code ONCE
    python -m nyc_events.users revoke <name>  # tombstone + delete their tokens
    python -m nyc_events.users list           # users + token counts
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sqlite3
import sys

from . import config, db

# Codes are machine-generated at ~192 bits (never human-chosen), so PBKDF2 is
# belt-and-braces against an oauth.db leak, not the primary defense. The
# iteration count is kept modest because the consent POST verifies against
# every active user's hash per attempt (bounded by the per-IP rate limiter).
_PBKDF2_ITERATIONS = 100_000
_SALT_BYTES = 16


def generate_passcode() -> str:
    return secrets.token_urlsafe(24)


def hash_passcode(code: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", code.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_passcode(code: str, stored: str) -> bool:
    try:
        scheme, iters_s, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    computed = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(computed, expected)


def match_user(conn: sqlite3.Connection, presented: str) -> str | None:
    """user_id of the non-revoked user whose passcode matches, else None.

    Every candidate hash is checked (no early exit) so response time doesn't
    depend on which user matched.
    """
    if not presented:
        return None
    matched: str | None = None
    for user_id, passcode_hash in db.active_user_passcodes(conn):
        if verify_passcode(presented, passcode_hash):
            matched = user_id
    return matched


def new_user_id() -> str:
    return "user-" + secrets.token_urlsafe(8)


# ---- CLI ---------------------------------------------------------------------


def _cmd_add(conn: sqlite3.Connection, name: str) -> int:
    code = generate_passcode()
    try:
        db.create_user(
            conn, user_id=new_user_id(), name=name, passcode_hash=hash_passcode(code)
        )
    except sqlite3.IntegrityError:
        print(f"error: user {name!r} already exists", file=sys.stderr)
        return 1
    print(f"Created user {name!r}. Invite code (shown ONCE, only a hash is stored):")
    print()
    print(f"    {code}")
    print()
    print("They paste this on the consent page when adding the connector.")
    return 0


def _cmd_revoke(conn: sqlite3.Connection, name: str) -> int:
    row = db.get_user_by_name(conn, name)
    if row is None:
        print(f"error: no user named {name!r}", file=sys.stderr)
        return 1
    if row["revoked_at"] is not None:
        print(f"user {name!r} was already revoked at {row['revoked_at']}")
        return 0
    deleted = db.revoke_user(conn, row["user_id"])
    print(
        f"Revoked {name!r}: invite code disabled, {deleted} access token(s) "
        "deleted. Live sessions expire within ~5 minutes (server token cache)."
    )
    return 0


def _cmd_list(conn: sqlite3.Connection) -> int:
    rows = db.list_users(conn)
    if not rows:
        print("no users (add one with: python -m nyc_events.users add <name>)")
        return 0
    for r in rows:
        status = f"REVOKED {r['revoked_at']}" if r["revoked_at"] else "active"
        last = r["last_token_issued_at"] or "never"
        print(
            f"{r['name']:<20} {status:<35} tokens={r['token_count']} "
            f"last_issued={last} created={r['created_at']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nyc_events.users",
        description="Manage per-person invite codes for the consent page.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add", help="create a user and print their invite code once")
    p_add.add_argument("name")
    p_revoke = sub.add_parser(
        "revoke", help="disable a user's code and delete their access tokens"
    )
    p_revoke.add_argument("name")
    sub.add_parser("list", help="list users with token counts")
    args = parser.parse_args(argv)

    db.init_oauth(config.OAUTH_DB_PATH)
    with db.connect_oauth(config.OAUTH_DB_PATH) as conn:
        if args.command == "add":
            return _cmd_add(conn, args.name)
        if args.command == "revoke":
            return _cmd_revoke(conn, args.name)
        return _cmd_list(conn)


if __name__ == "__main__":
    raise SystemExit(main())
