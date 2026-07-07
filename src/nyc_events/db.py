"""SQLite store: schema, FTS5, upsert, prune, search."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import statistics
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from .models import Borough, Event, Price

EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    url TEXT,
    start_dt TEXT NOT NULL,
    end_dt TEXT,
    venue_name TEXT,
    borough TEXT,
    neighborhood TEXT,
    lat REAL,
    lng REAL,
    age_min INTEGER,
    age_max INTEGER,
    price TEXT NOT NULL DEFAULT 'unknown',
    tags TEXT,
    raw_payload TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    missing_since TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_dt);
CREATE INDEX IF NOT EXISTS idx_events_borough ON events(borough);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    title, description, venue_name, neighborhood, tags,
    content='events', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, title, description, venue_name, neighborhood, tags)
    VALUES (new.rowid, new.title, new.description, new.venue_name, new.neighborhood, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, description, venue_name, neighborhood, tags)
    VALUES('delete', old.rowid, old.title, old.description, old.venue_name,
           old.neighborhood, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, description, venue_name, neighborhood, tags)
    VALUES('delete', old.rowid, old.title, old.description, old.venue_name,
           old.neighborhood, old.tags);
    INSERT INTO events_fts(rowid, title, description, venue_name, neighborhood, tags)
    VALUES (new.rowid, new.title, new.description, new.venue_name, new.neighborhood, new.tags);
END;

-- Geocode cache for the enrichment pass (nyc_events.enrich). Keyed by an
-- opaque lookup string (forward = "fwd:<normalized venue|borough>", reverse =
-- "rev:<rounded lat,lng>"). No TTL: venue locations are stable, so a hit
-- never re-queries the US Census geocoder. Lives here (not oauth.db) because
-- it's event-derived data; namespaced by the lookup_key prefix.
CREATE TABLE IF NOT EXISTS geocode_cache (
    lookup_key TEXT PRIMARY KEY,
    lat REAL,
    lng REAL,
    nta_name TEXT,
    resolved_at TEXT NOT NULL
);

-- Per-source ingest telemetry (issue #65). One row per source per nightly run
-- so a source that quietly stops yielding (upstream redesign, a feed cap
-- starting to truncate) is visible as a drop in `fetched` over time instead of
-- inferred from mutated `last_seen`. `run_id` groups all sources in one run.
-- Plain CREATE TABLE (idempotent on its own), not a _migrate_* column-add.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    outcome TEXT NOT NULL,          -- 'ok' | 'fetch_failed' | 'upsert_failed'
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    marked_missing INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_source ON ingest_runs(source, id);
"""

# OAuth state lives in a separate SQLite file (data/oauth.db) so that wiping
# data/events.db during ingest iteration does NOT invalidate the access tokens
# claude.ai has cached for the custom connector — that previously caused the
# user to re-paste the master token on every dev DB reset.
OAUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    access_token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    scope TEXT,
    issued_at TEXT NOT NULL,
    expires_at TEXT
);

-- Per-person invite codes (MULTI-USER-PLAN.md Phase A). Only the salted hash
-- of a user's passcode is stored; the plaintext code is printed exactly once
-- by `python -m nyc_events.users add`. revoked_at is a tombstone, not a
-- delete, so attribution on old tokens survives revocation.
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    passcode_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
"""


def _connect(path: str) -> sqlite3.Connection:
    """Plain open: WAL + row_factory + FK pragma, NO DDL. The schema must
    already exist — call init_events()/init_oauth() once at process startup
    (server build_app / each CLI entry point) so the per-request read path
    never re-runs schema DDL and takes a write lock contending with ingest."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_events(path: str) -> None:
    """Create the events schema + run idempotent migrations. Call once at
    startup, off the request path (see _connect). Safe to re-run."""
    conn = _connect(path)
    try:
        conn.executescript(EVENTS_SCHEMA)
        _migrate_events(conn)
    finally:
        conn.close()


def init_oauth(path: str) -> None:
    """Create the oauth schema + run idempotent migrations. Call once at
    startup, off the request path. Safe to re-run."""
    conn = _connect(path)
    try:
        conn.executescript(OAUTH_SCHEMA)
        _migrate_oauth(conn)
    finally:
        conn.close()


def _migrate_events(conn: sqlite3.Connection) -> None:
    # Idempotent column adds. sqlite3 PRAGMA table_info gives existing columns;
    # ALTER TABLE ADD COLUMN is no-op-safe via this check. Add new optional
    # columns here in future patches the same way.
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "raw_payload" not in existing:
        conn.execute("ALTER TABLE events ADD COLUMN raw_payload TEXT")
        conn.commit()
    if "missing_since" not in existing:
        conn.execute("ALTER TABLE events ADD COLUMN missing_since TEXT")
        conn.commit()


def _migrate_oauth(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)")}
    if "expires_at" not in existing:
        # Pre-existing tokens get NULL expires_at, which is_valid_oauth_token
        # treats as "no expiry recorded" — they keep working until manually
        # revoked. Newly issued tokens (post-fix) always get an expires_at.
        conn.execute("ALTER TABLE oauth_tokens ADD COLUMN expires_at TEXT")
        conn.commit()
    if "user_id" not in existing:
        # Attribution for multi-user (MULTI-USER-PLAN.md Phase A). Tokens
        # issued before the users table existed get NULL — they're the
        # operator's own claude.ai sessions and stay valid.
        conn.execute("ALTER TABLE oauth_tokens ADD COLUMN user_id TEXT")
        conn.commit()
    # Phase B: tokens are stored hashed at rest (see hash_access_token). Any
    # legacy plaintext row is hashed in place, once — the "sha256:" prefix
    # makes the rewrite idempotent, and the client keeps presenting the same
    # plaintext bearer so nothing is logged out.
    legacy = conn.execute(
        "SELECT access_token FROM oauth_tokens "
        "WHERE access_token NOT LIKE 'sha256:%'"
    ).fetchall()
    for row in legacy:
        conn.execute(
            "UPDATE oauth_tokens SET access_token = ? WHERE access_token = ?",
            (hash_access_token(row["access_token"]), row["access_token"]),
        )
    if legacy:
        conn.commit()


@contextmanager
def connect_events(path: str):
    """Plain per-call connection to the events DB. Assumes init_events() has
    already run for this path (server startup / CLI entry point)."""
    conn = _connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def connect_oauth(path: str):
    """Plain per-call connection to the oauth DB. Assumes init_oauth() has
    already run for this path (server startup)."""
    conn = _connect(path)
    try:
        yield conn
    finally:
        conn.close()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(UTC).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def upsert_events(conn: sqlite3.Connection, events: Iterable[Event]) -> tuple[int, int]:
    now = datetime.now(UTC).isoformat()
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    processed = 0
    for ev in events:
        conn.execute(
            """
            INSERT INTO events (
                id, source, external_id, title, description, url,
                start_dt, end_dt, venue_name, borough, neighborhood,
                lat, lng, age_min, age_max, price, tags, raw_payload,
                first_seen, last_seen, missing_since
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(id) DO UPDATE SET
                source       = excluded.source,
                external_id  = excluded.external_id,
                title        = excluded.title,
                description  = excluded.description,
                url          = excluded.url,
                start_dt     = excluded.start_dt,
                end_dt       = excluded.end_dt,
                venue_name   = excluded.venue_name,
                borough      = excluded.borough,
                -- neighborhood / lat / lng are enrichment-managed (sources
                -- almost always yield NULL; enrich.py fills them in a second
                -- pass). A source-provided value always wins; otherwise keep
                -- the enriched value so one failed enrich pass can't blank
                -- the whole catalog's neighborhoods for a day — UNLESS the
                -- row's location identity (venue or borough) changed this
                -- ingest, in which case the stale coding is reset to NULL so
                -- tonight's enrich re-resolves it. (IS NOT = null-safe
                -- "is distinct from"; bare column names = the existing row.)
                neighborhood = CASE
                    WHEN excluded.neighborhood IS NOT NULL THEN excluded.neighborhood
                    WHEN excluded.venue_name IS NOT venue_name
                      OR excluded.borough IS NOT borough THEN NULL
                    ELSE neighborhood
                END,
                lat = CASE
                    WHEN excluded.lat IS NOT NULL THEN excluded.lat
                    WHEN excluded.venue_name IS NOT venue_name
                      OR excluded.borough IS NOT borough THEN NULL
                    ELSE lat
                END,
                lng = CASE
                    WHEN excluded.lng IS NOT NULL THEN excluded.lng
                    WHEN excluded.venue_name IS NOT venue_name
                      OR excluded.borough IS NOT borough THEN NULL
                    ELSE lng
                END,
                age_min      = excluded.age_min,
                age_max      = excluded.age_max,
                price        = excluded.price,
                tags         = excluded.tags,
                raw_payload  = COALESCE(excluded.raw_payload, raw_payload),
                last_seen    = excluded.last_seen,
                missing_since = NULL
            """,
            (
                ev.id,
                ev.source,
                ev.external_id,
                ev.title,
                ev.description,
                ev.url,
                _iso(ev.start_dt),
                _iso(ev.end_dt),
                ev.venue_name,
                ev.borough.value if ev.borough else None,
                ev.neighborhood,
                ev.lat,
                ev.lng,
                ev.age_min,
                ev.age_max,
                ev.price.value,
                json.dumps(ev.tags),
                ev.raw_payload,
                now,
                now,
            ),
        )
        processed += 1
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    inserted = after - before
    updated = processed - inserted
    return inserted, updated


def prune_stale(conn: sqlite3.Connection, before: datetime) -> int:
    if before.tzinfo is None:
        raise ValueError("prune cutoff must be tz-aware")
    cutoff = before.astimezone(UTC).isoformat()
    cur = conn.execute(
        "DELETE FROM events WHERE COALESCE(end_dt, start_dt) < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


def count_future_events(conn: sqlite3.Connection, source: str, now: datetime) -> int:
    """Number of stored future events for one source. The ingest circuit
    breaker compares this baseline against the fetched count to detect
    silently-incomplete fetches before any missing-marking happens."""
    return conn.execute(
        "SELECT COUNT(*) FROM events WHERE source = ? AND start_dt > ?",
        (source, _iso(now)),
    ).fetchone()[0]


def mark_missing(
    conn: sqlite3.Connection,
    *,
    source: str,
    run_start: datetime,
    window_days: int,
) -> int:
    """Stamp missing_since on rows a successful ingest run should have
    re-seen but didn't — the upstream may have cancelled them.

    Only stamps rows that are: from this source, currently unstamped (a
    prior stamp keeps its original timestamp so the user-facing grace
    period is measured from the FIRST miss), not re-seen this run
    (last_seen predates run_start), in the future, and inside the window
    the source actually fetched — minus one day of margin, because some
    sources truncate their window end to a date boundary.

    The stamp is cleared by upsert_events the moment any later run sees
    the event again, so false positives self-heal on the next ingest.
    Callers must gate on the source's fetch having succeeded AND looking
    complete (see ingest._fetch_looks_complete); this function does no
    sanity checking of its own.
    """
    if run_start.tzinfo is None:
        raise ValueError("run_start must be tz-aware")
    run_iso = run_start.astimezone(UTC).isoformat()
    window_end = (run_start + timedelta(days=window_days - 1)).astimezone(UTC)
    cur = conn.execute(
        """
        UPDATE events SET missing_since = ?
        WHERE source = ?
          AND missing_since IS NULL
          AND last_seen < ?
          AND start_dt > ?
          AND start_dt < ?
        """,
        (run_iso, source, run_iso, run_iso, _iso(window_end)),
    )
    conn.commit()
    return cur.rowcount


def _row_to_event(row: sqlite3.Row) -> Event:
    # raw_payload / missing_since columns may not exist on very old DBs from
    # before their migrations; defend against that.
    raw_payload = row["raw_payload"] if "raw_payload" in row.keys() else None
    missing_since = row["missing_since"] if "missing_since" in row.keys() else None
    return Event(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        title=row["title"],
        description=row["description"],
        url=row["url"],
        start_dt=_parse_iso(row["start_dt"]),
        end_dt=_parse_iso(row["end_dt"]),
        venue_name=row["venue_name"],
        borough=Borough(row["borough"]) if row["borough"] else None,
        neighborhood=row["neighborhood"],
        lat=row["lat"],
        lng=row["lng"],
        age_min=row["age_min"],
        age_max=row["age_max"],
        price=Price(row["price"]),
        tags=json.loads(row["tags"]) if row["tags"] else [],
        raw_payload=raw_payload,
        missing_since=_parse_iso(missing_since),
    )


def get_event_by_id(conn: sqlite3.Connection, event_id: str) -> Event | None:
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_event(row) if row is not None else None


def _fts_query(q: str) -> str:
    # Token-prefix match so "story" matches "stories", "muse" matches "museum".
    # FTS5 requires escaping double-quotes in literal terms.
    terms = [t.replace('"', '""') for t in q.split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def search(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    borough: str | None = None,
    neighborhood: str | None = None,
    age: int | None = None,
    free_only: bool = False,
    source: str | None = None,
    exclude_low_confidence: bool = False,
    start_after: datetime | None = None,
    start_before: datetime | None = None,
    limit: int = 25,
) -> list[Event]:
    sql = "SELECT e.* FROM events e"
    params: list = []
    where: list[str] = []

    if query:
        # A whitespace-only query is truthy but tokenizes to no terms; an empty
        # MATCH string is an FTS5 syntax error. Fall through to a text-unfiltered
        # (date/facet-only) search instead of raising (issue #61).
        fts = _fts_query(query)
        if fts:
            sql += " JOIN events_fts f ON f.rowid = e.rowid"
            where.append("events_fts MATCH ?")
            params.append(fts)

    if borough:
        where.append("e.borough = ?")
        params.append(borough)

    if source:
        where.append("e.source = ?")
        params.append(source)

    if exclude_low_confidence:
        # low_confidence (in the tool projection) is description IS NULL AND
        # url IS NULL — i.e. permit-style rows with no public-facing detail.
        # Excluding it keeps either field present.
        where.append("(e.description IS NOT NULL OR e.url IS NOT NULL)")

    if neighborhood:
        # Case-insensitive substring so a colloquial label ("Crown Heights")
        # matches the official NTA names it prefixes ("Crown Heights (North)").
        where.append("e.neighborhood LIKE ? ESCAPE '\\'")
        esc = neighborhood.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{esc}%")

    if age is not None:
        where.append("(e.age_min IS NULL OR e.age_min <= ?)")
        where.append("(e.age_max IS NULL OR e.age_max >= ?)")
        params.extend([age, age])

    if free_only:
        where.append("e.price = 'free'")

    if start_after is not None:
        where.append("e.start_dt >= ?")
        params.append(_iso(start_after))

    if start_before is not None:
        where.append("e.start_dt <= ?")
        params.append(_iso(start_before))

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += " ORDER BY e.start_dt LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_event(r) for r in rows]


def get_geocode(
    conn: sqlite3.Connection, lookup_key: str
) -> tuple[float | None, float | None, str | None] | None:
    """Return (lat, lng, nta_name) for a cached lookup, or None on a miss.
    A cached row with all-NULL values is a remembered negative result (the
    geocoder couldn't resolve it) — still a hit, so we don't re-query."""
    row = conn.execute(
        "SELECT lat, lng, nta_name FROM geocode_cache WHERE lookup_key = ?",
        (lookup_key,),
    ).fetchone()
    if row is None:
        return None
    return row["lat"], row["lng"], row["nta_name"]


def put_geocode(
    conn: sqlite3.Connection,
    lookup_key: str,
    lat: float | None,
    lng: float | None,
    nta_name: str | None,
) -> None:
    conn.execute(
        "INSERT INTO geocode_cache (lookup_key, lat, lng, nta_name, resolved_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(lookup_key) DO UPDATE SET "
        "lat = excluded.lat, lng = excluded.lng, "
        "nta_name = excluded.nta_name, resolved_at = excluded.resolved_at",
        (lookup_key, lat, lng, nta_name, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def hash_access_token(token: str) -> str:
    """At-rest form of an access token (MULTI-USER-PLAN.md Phase B): a leaked
    oauth.db backup must not leak live bearer credentials. Plain SHA-256 (no
    salt/stretching) is right here — tokens are 384-bit random strings, not
    passwords. The prefix marks a value as already hashed so the one-time
    migration in _migrate_oauth is idempotent."""
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def store_oauth_token(
    conn: sqlite3.Connection,
    access_token: str,
    client_id: str,
    scope: str | None = None,
    expires_at: datetime | None = None,
    user_id: str | None = None,
) -> None:
    """Persist a newly issued token. Only the hash is stored; the caller is
    responsible for returning the plaintext to the client exactly once."""
    now = datetime.now(UTC)
    conn.execute(
        "INSERT INTO oauth_tokens "
        "(access_token, client_id, scope, issued_at, expires_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            hash_access_token(access_token),
            client_id,
            scope,
            now.isoformat(),
            _iso(expires_at) if expires_at is not None else None,
            user_id,
        ),
    )
    conn.commit()


def is_valid_oauth_token(conn: sqlite3.Connection, access_token: str) -> bool:
    """Takes the plaintext bearer as presented on the wire; the lookup is by
    its at-rest hash."""
    row = conn.execute(
        "SELECT expires_at FROM oauth_tokens WHERE access_token = ?",
        (hash_access_token(access_token),),
    ).fetchone()
    if row is None:
        return False
    expires_at = row["expires_at"]
    if expires_at is None:
        # Legacy row (issued before expiry tracking existed). Treat as valid
        # — manual `DELETE FROM oauth_tokens` is still how you revoke these.
        return True
    return datetime.fromisoformat(expires_at) > datetime.now(UTC)


# ---- users (per-person invite codes; MULTI-USER-PLAN.md Phase A) ------------


def create_user(
    conn: sqlite3.Connection, *, user_id: str, name: str, passcode_hash: str
) -> None:
    """Insert a new user. Raises sqlite3.IntegrityError on a duplicate name —
    the CLI surfaces that instead of silently replacing someone's code."""
    conn.execute(
        "INSERT INTO users (user_id, name, passcode_hash, created_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, name, passcode_hash, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def get_user_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE name = ?", (name,)
    ).fetchone()


def active_user_passcodes(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(user_id, passcode_hash) for every non-revoked user. The consent flow
    iterates these to find whose code was presented — fine at friends-and-
    family scale; don't add an index-by-code scheme for it."""
    return [
        (r["user_id"], r["passcode_hash"])
        for r in conn.execute(
            "SELECT user_id, passcode_hash FROM users WHERE revoked_at IS NULL"
        )
    ]


def revoke_user(conn: sqlite3.Connection, user_id: str) -> int:
    """Tombstone the user AND delete their access tokens (both halves of
    revocation — the code stops minting new tokens, and existing sessions
    die within the auth.py token-cache TTL). Returns tokens deleted."""
    conn.execute(
        "UPDATE users SET revoked_at = ? WHERE user_id = ?",
        (datetime.now(UTC).isoformat(), user_id),
    )
    cur = conn.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT u.user_id, u.name, u.created_at, u.revoked_at,
               COUNT(t.access_token) AS token_count,
               MAX(t.issued_at) AS last_token_issued_at
        FROM users u
        LEFT JOIN oauth_tokens t ON t.user_id = u.user_id
        GROUP BY u.user_id
        ORDER BY u.created_at
        """
    ).fetchall()
    return [dict(r) for r in rows]


def list_sources(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT source,
               COUNT(*) AS event_count,
               MIN(start_dt) AS earliest_event,
               MAX(start_dt) AS latest_event,
               MAX(last_seen) AS last_seen
        FROM events
        GROUP BY source
        ORDER BY source
        """
    ).fetchall()
    return [dict(r) for r in rows]


# ---- ingest telemetry (issue #65) -------------------------------------------


def record_ingest_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source: str,
    started_at: datetime,
    finished_at: datetime,
    outcome: str,
    fetched: int,
    inserted: int,
    updated: int,
    marked_missing: int,
) -> None:
    """Append one source's result for a nightly run. `duration_s` is derived
    from the timestamps. Commits immediately so a later source crashing can't
    lose earlier sources' telemetry."""
    duration = (finished_at - started_at).total_seconds()
    conn.execute(
        "INSERT INTO ingest_runs (run_id, source, started_at, finished_at, "
        "outcome, fetched, inserted, updated, marked_missing, duration_s) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            source,
            _iso(started_at),
            _iso(finished_at),
            outcome,
            fetched,
            inserted,
            updated,
            marked_missing,
            duration,
        ),
    )
    conn.commit()


def fetch_drift_baseline(
    conn: sqlite3.Connection,
    source: str,
    *,
    window: int = 7,
    min_history: int = 3,
) -> float | None:
    """Median `fetched` over this source's most recent successful runs, or
    None when there isn't enough history to judge (fewer than `min_history`
    prior 'ok' runs). Callers compare the current fetch against this to catch a
    source that quietly stopped yielding. Query the baseline BEFORE recording
    the current run so it reflects prior runs only."""
    counts = [
        r["fetched"]
        for r in conn.execute(
            "SELECT fetched FROM ingest_runs WHERE source = ? AND outcome = 'ok' "
            "ORDER BY id DESC LIMIT ?",
            (source, window),
        )
    ]
    if len(counts) < min_history:
        return None
    return statistics.median(counts)


def list_facets(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Distinct values currently present in the DB for the search facets a
    caller would filter on: boroughs, neighborhoods, tags, and sources.

    Lets a client discover valid filter values instead of guessing. Reflects
    only what's actually ingested right now, so it tracks the live data (e.g.
    a neighborhood with zero current events won't appear). Tags are stored as
    a JSON array per row, so they're unpacked and de-duplicated in Python
    rather than via a json1 query, keeping the dependency surface unchanged.
    """
    boroughs = [
        r["borough"] for r in conn.execute(
            "SELECT DISTINCT borough FROM events "
            "WHERE borough IS NOT NULL ORDER BY borough"
        )
    ]
    neighborhoods = [
        r["neighborhood"] for r in conn.execute(
            "SELECT DISTINCT neighborhood FROM events "
            "WHERE neighborhood IS NOT NULL ORDER BY neighborhood"
        )
    ]
    sources = [
        r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM events ORDER BY source"
        )
    ]
    tagset: set[str] = set()
    for r in conn.execute("SELECT tags FROM events WHERE tags IS NOT NULL"):
        try:
            tagset.update(json.loads(r["tags"]))
        except (json.JSONDecodeError, TypeError):
            continue
    return {
        "boroughs": boroughs,
        "neighborhoods": neighborhoods,
        "tags": sorted(tagset),
        "sources": sources,
    }
