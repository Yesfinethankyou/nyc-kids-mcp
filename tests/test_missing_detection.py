"""Missing-event (possible-cancellation) detection.

Covers the layered design:
- db.mark_missing stamps only unseen, future, in-window rows for one source,
  and never re-stamps (grace is measured from the FIRST miss).
- upsert_events clears the stamp the moment an event is seen again (self-heal).
- ingest._fetch_looks_complete circuit breaker rejects empty/short fetches.
- tools._possibly_cancelled only surfaces after the grace period.
- Only full-window sources opt in (mommy_poppins must stay excluded).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nyc_events import db
from nyc_events.ingest import _fetch_looks_complete
from nyc_events.models import Borough, Event, Price, compute_id
from nyc_events.sources import ENABLED_SOURCES
from nyc_events.sources.governors_island import GovernorsIslandSource
from nyc_events.sources.mommy_poppins import MommyPoppinsSource
from nyc_events.tools import _possibly_cancelled

# Sources that intentionally opt OUT of missing-detection (window_days is None)
# because a fetch is not a guaranteed full re-fetch of the window:
#   - MommyPoppins: incremental sitemap-lastmod discovery (unseen != cancelled).
#   - GovernorsIsland: feed hard-caps at 100 rows ordered id-asc with no
#     pagination, so newer events can scroll past the cap rather than being
#     cancelled.
_MISSING_DETECTION_EXCLUDED = (MommyPoppinsSource, GovernorsIslandSource)

UTC = UTC

WINDOW_DAYS = 60


def _ev(external_id: str, *, days_ahead: float = 7, source: str = "testsrc") -> Event:
    start = datetime.now(UTC) + timedelta(days=days_ahead)
    return Event(
        id=compute_id(source, external_id=external_id),
        source=source,
        external_id=external_id,
        title=f"Event {external_id}",
        start_dt=start,
        end_dt=start + timedelta(hours=1),
        venue_name="Prospect Park",
        borough=Borough.BROOKLYN,
        price=Price.FREE,
        tags=["family"],
    )


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_events(path)
    with db.connect_events(path) as c:
        yield c


def _missing_since(conn, event_id: str) -> str | None:
    return conn.execute(
        "SELECT missing_since FROM events WHERE id = ?", (event_id,)
    ).fetchone()[0]


def _mark(conn, *, source: str = "testsrc", run_start: datetime | None = None) -> int:
    return db.mark_missing(
        conn,
        source=source,
        run_start=run_start or datetime.now(UTC),
        window_days=WINDOW_DAYS,
    )


# --- db.mark_missing -----------------------------------------------------


def test_marks_unseen_future_event(conn):
    a, b = _ev("a"), _ev("b")
    db.upsert_events(conn, [a, b])
    run_start = datetime.now(UTC)
    db.upsert_events(conn, [a])  # second run: only a re-seen
    assert _mark(conn, run_start=run_start) == 1
    assert _missing_since(conn, a.id) is None
    assert _missing_since(conn, b.id) is not None


def test_does_not_mark_other_sources(conn):
    other = _ev("x", source="othersrc")
    db.upsert_events(conn, [other])
    assert _mark(conn, run_start=datetime.now(UTC)) == 0
    assert _missing_since(conn, other.id) is None


def test_does_not_mark_past_events(conn):
    past = _ev("past", days_ahead=-2)
    db.upsert_events(conn, [past])
    assert _mark(conn, run_start=datetime.now(UTC)) == 0


def test_does_not_mark_beyond_window(conn):
    far = _ev("far", days_ahead=WINDOW_DAYS + 10)
    near_boundary = _ev("boundary", days_ahead=WINDOW_DAYS - 0.5)
    db.upsert_events(conn, [far, near_boundary])
    # Both unseen by the "second run" — only rows safely inside the window
    # (with the 1-day end margin) may be stamped.
    assert _mark(conn, run_start=datetime.now(UTC)) == 0


def test_first_stamp_is_preserved_on_repeat_marks(conn):
    a = _ev("a")
    db.upsert_events(conn, [a])
    first_run = datetime.now(UTC)
    assert _mark(conn, run_start=first_run) == 1
    stamp1 = _missing_since(conn, a.id)
    # A later run that still doesn't see the event must not refresh the
    # stamp — the grace period counts from the first miss.
    assert _mark(conn, run_start=first_run + timedelta(days=1)) == 0
    assert _missing_since(conn, a.id) == stamp1


def test_naive_run_start_rejected(conn):
    with pytest.raises(ValueError):
        db.mark_missing(
            conn, source="testsrc", run_start=datetime(2026, 6, 1), window_days=60
        )


# --- self-heal via upsert ------------------------------------------------


def test_upsert_clears_stamp_when_event_reappears(conn):
    a = _ev("a")
    db.upsert_events(conn, [a])
    assert _mark(conn, run_start=datetime.now(UTC)) == 1
    assert _missing_since(conn, a.id) is not None
    db.upsert_events(conn, [a])  # event re-seen
    assert _missing_since(conn, a.id) is None


def test_row_to_event_carries_missing_since(conn):
    a = _ev("a")
    db.upsert_events(conn, [a])
    _mark(conn, run_start=datetime.now(UTC))
    loaded = db.get_event_by_id(conn, a.id)
    assert loaded.missing_since is not None
    assert loaded.missing_since.tzinfo is not None


# --- migration -----------------------------------------------------------


def test_migration_adds_missing_since_column(tmp_path):
    import sqlite3

    p = str(tmp_path / "old.db")
    legacy = sqlite3.connect(p)
    legacy.executescript(
        """
        CREATE TABLE events (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT,
            title TEXT NOT NULL, description TEXT, url TEXT,
            start_dt TEXT NOT NULL, end_dt TEXT, venue_name TEXT,
            borough TEXT, neighborhood TEXT, lat REAL, lng REAL,
            age_min INTEGER, age_max INTEGER,
            price TEXT NOT NULL DEFAULT 'unknown', tags TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        );
        """
    )
    legacy.commit()
    legacy.close()
    db.init_events(p)  # schema DDL + migrations now live in init, not connect
    with db.connect_events(p) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "missing_since" in cols
    assert "raw_payload" in cols  # older migration still applies too


# --- ingest circuit breaker ----------------------------------------------


def test_breaker_trips_on_empty_fetch():
    assert not _fetch_looks_complete(0, 0)
    assert not _fetch_looks_complete(0, 300)


def test_breaker_trips_on_short_fetch():
    assert not _fetch_looks_complete(100, 300)  # under half the baseline


def test_breaker_passes_normal_fetch():
    assert _fetch_looks_complete(290, 300)
    assert _fetch_looks_complete(150, 300)  # exactly half passes
    assert _fetch_looks_complete(20, 0)  # brand-new source, no baseline


# --- server grace period -------------------------------------------------


def test_no_stamp_is_not_cancelled():
    assert not _possibly_cancelled(_ev("a"))


def test_fresh_stamp_within_grace_is_not_surfaced():
    ev = _ev("a").model_copy(
        update={"missing_since": datetime.now(UTC) - timedelta(hours=2)}
    )
    assert not _possibly_cancelled(ev)


def test_stamp_older_than_grace_is_surfaced():
    ev = _ev("a").model_copy(
        update={"missing_since": datetime.now(UTC) - timedelta(hours=31)}
    )
    assert _possibly_cancelled(ev)


# --- per-source opt-in ---------------------------------------------------


def test_mommy_poppins_is_excluded():
    # Sitemap-lastmod discovery is incremental: unseen != cancelled. This
    # source must never participate in missing-detection.
    assert MommyPoppinsSource().window_days is None


def test_governors_island_is_excluded():
    # The things-to-do feed hard-caps at 100 rows ordered id-asc with no
    # pagination, so a fetch is not a guaranteed full window re-fetch — newer
    # events can scroll past the cap. Opting in would falsely flag them.
    assert GovernorsIslandSource().window_days is None


def test_full_window_sources_opt_in():
    opted_in = {
        cls.__name__: cls().window_days
        for cls in ENABLED_SOURCES
        if cls not in _MISSING_DETECTION_EXCLUDED
    }
    # nycgovparks_events mirrors its server-side window ("today → end of next
    # month", ~55-61 days depending on the calendar) with the conservative
    # lower bound; new_york_family walks a deliberately short 35-day window
    # (one-plus requests per day against a 16-row-capped API — see the module
    # docstring); every other full-window source uses the 60-day convention.
    expected_days = {"NYCGovParksEventsSource": 55, "NewYorkFamilySource": 35}
    assert all(
        days == expected_days.get(name, 60) for name, days in opted_in.items()
    ), opted_in
    assert len(opted_in) == 11
