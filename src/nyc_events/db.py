"""SQLite store: schema, FTS5, upsert, prune, search."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone

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
    last_seen TEXT NOT NULL
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
    VALUES('delete', old.rowid, old.title, old.description, old.venue_name, old.neighborhood, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, description, venue_name, neighborhood, tags)
    VALUES('delete', old.rowid, old.title, old.description, old.venue_name, old.neighborhood, old.tags);
    INSERT INTO events_fts(rowid, title, description, venue_name, neighborhood, tags)
    VALUES (new.rowid, new.title, new.description, new.venue_name, new.neighborhood, new.tags);
END;
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
"""


def _open(path: str, schema: str) -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(schema)
    return conn


def _migrate_events(conn: sqlite3.Connection) -> None:
    # Idempotent column adds. sqlite3 PRAGMA table_info gives existing columns;
    # ALTER TABLE ADD COLUMN is no-op-safe via this check. Add new optional
    # columns here in future patches the same way.
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "raw_payload" not in existing:
        conn.execute("ALTER TABLE events ADD COLUMN raw_payload TEXT")
        conn.commit()


def _migrate_oauth(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)")}
    if "expires_at" not in existing:
        # Pre-existing tokens get NULL expires_at, which is_valid_oauth_token
        # treats as "no expiry recorded" — they keep working until manually
        # revoked. Newly issued tokens (post-fix) always get an expires_at.
        conn.execute("ALTER TABLE oauth_tokens ADD COLUMN expires_at TEXT")
        conn.commit()


@contextmanager
def connect_events(path: str):
    conn = _open(path, EVENTS_SCHEMA)
    try:
        _migrate_events(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def connect_oauth(path: str):
    conn = _open(path, OAUTH_SCHEMA)
    try:
        _migrate_oauth(conn)
        yield conn
    finally:
        conn.close()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def upsert_events(conn: sqlite3.Connection, events: Iterable[Event]) -> tuple[int, int]:
    now = datetime.now(timezone.utc).isoformat()
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    processed = 0
    for ev in events:
        conn.execute(
            """
            INSERT INTO events (
                id, source, external_id, title, description, url,
                start_dt, end_dt, venue_name, borough, neighborhood,
                lat, lng, age_min, age_max, price, tags, raw_payload,
                first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                neighborhood = excluded.neighborhood,
                lat          = excluded.lat,
                lng          = excluded.lng,
                age_min      = excluded.age_min,
                age_max      = excluded.age_max,
                price        = excluded.price,
                tags         = excluded.tags,
                raw_payload  = COALESCE(excluded.raw_payload, raw_payload),
                last_seen    = excluded.last_seen
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
    cutoff = before.astimezone(timezone.utc).isoformat()
    cur = conn.execute(
        "DELETE FROM events WHERE COALESCE(end_dt, start_dt) < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


def _row_to_event(row: sqlite3.Row) -> Event:
    # raw_payload column may not exist on very old DBs from before the
    # migration; defend against that.
    raw_payload = row["raw_payload"] if "raw_payload" in row.keys() else None
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
    age: int | None = None,
    free_only: bool = False,
    start_after: datetime | None = None,
    start_before: datetime | None = None,
    limit: int = 25,
) -> list[Event]:
    sql = "SELECT e.* FROM events e"
    params: list = []
    where: list[str] = []

    if query:
        sql += " JOIN events_fts f ON f.rowid = e.rowid"
        where.append("events_fts MATCH ?")
        params.append(_fts_query(query))

    if borough:
        where.append("e.borough = ?")
        params.append(borough)

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


def store_oauth_token(
    conn: sqlite3.Connection,
    access_token: str,
    client_id: str,
    scope: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO oauth_tokens (access_token, client_id, scope, issued_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            access_token,
            client_id,
            scope,
            now.isoformat(),
            _iso(expires_at) if expires_at is not None else None,
        ),
    )
    conn.commit()


def is_valid_oauth_token(conn: sqlite3.Connection, access_token: str) -> bool:
    row = conn.execute(
        "SELECT expires_at FROM oauth_tokens WHERE access_token = ?",
        (access_token,),
    ).fetchone()
    if row is None:
        return False
    expires_at = row["expires_at"]
    if expires_at is None:
        # Legacy row (issued before expiry tracking existed). Treat as valid
        # — manual `DELETE FROM oauth_tokens` is still how you revoke these.
        return True
    return datetime.fromisoformat(expires_at) > datetime.now(timezone.utc)


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
