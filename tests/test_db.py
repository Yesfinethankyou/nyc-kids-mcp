"""DB integration tests.

Covers what Phase 1 promised:
- upsert is idempotent (same input twice -> no duplicates, last_seen updated)
- search filters: borough, age window, free_only, date range
- FTS5 partial-word matching ("muse" matches "Museum")
- prune_stale removes only past events
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nyc_events import db
from nyc_events.models import Borough, Event, Price, compute_id

UTC = UTC


def _ev(**overrides):
    base = dict(
        source="testsrc",
        external_id=None,
        title="Toddler Music in Prospect Park",
        description="Sing-along for ages 1-4.",
        url="https://example.com/e1",
        start_dt=datetime(2026, 6, 15, 14, 0, tzinfo=UTC),
        end_dt=datetime(2026, 6, 15, 15, 0, tzinfo=UTC),
        venue_name="Prospect Park",
        borough=Borough.BROOKLYN,
        neighborhood="Prospect Heights",
        age_min=1,
        age_max=4,
        price=Price.FREE,
        tags=["music", "family"],
    )
    base.update(overrides)
    if "id" not in base:
        ext = base.get("external_id") or base["title"]
        base["id"] = compute_id(base["source"], external_id=str(ext))
    return Event(**base)


@pytest.fixture
def conn(tmp_path):
    with db.connect_events(str(tmp_path / "test.db")) as c:
        yield c


# --- upsert idempotency --------------------------------------------------


def test_upsert_inserts_once_then_updates(conn):
    e = _ev(external_id="e1")
    ins1, upd1 = db.upsert_events(conn, [e])
    assert (ins1, upd1) == (1, 0)
    ins2, upd2 = db.upsert_events(conn, [e])
    assert (ins2, upd2) == (0, 1)
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 1


def test_upsert_updates_changed_fields(conn):
    db.upsert_events(conn, [_ev(external_id="e1", title="Old Title")])
    db.upsert_events(conn, [_ev(external_id="e1", title="New Title")])
    title = conn.execute("SELECT title FROM events").fetchone()["title"]
    assert title == "New Title"


# --- search filters ------------------------------------------------------


def test_search_filters_by_borough(conn):
    db.upsert_events(conn, [
        _ev(external_id="b1", borough=Borough.BROOKLYN, title="Brooklyn event"),
        _ev(external_id="m1", borough=Borough.MANHATTAN, title="Manhattan event"),
    ])
    results = db.search(conn, borough="Brooklyn")
    assert len(results) == 1 and results[0].borough == Borough.BROOKLYN


def test_search_age_window_includes_in_range(conn):
    db.upsert_events(conn, [
        _ev(external_id="r1", age_min=1, age_max=4, title="Toddler"),
        _ev(external_id="r2", age_min=8, age_max=12, title="Older kids"),
        _ev(external_id="r3", age_min=None, age_max=None, title="All ages"),
    ])
    titles = {e.title for e in db.search(conn, age=4)}
    assert "Toddler" in titles
    assert "All ages" in titles  # missing range -> included
    assert "Older kids" not in titles


def test_search_free_only(conn):
    db.upsert_events(conn, [
        _ev(external_id="f1", price=Price.FREE, title="Free event"),
        _ev(external_id="p1", price=Price.PAID, title="Paid event"),
        _ev(external_id="u1", price=Price.UNKNOWN, title="Unknown price event"),
    ])
    titles = {e.title for e in db.search(conn, free_only=True)}
    assert titles == {"Free event"}


def test_search_date_range(conn):
    db.upsert_events(conn, [
        _ev(external_id="past", start_dt=datetime(2025, 1, 1, tzinfo=UTC), title="Past"),
        _ev(external_id="mid", start_dt=datetime(2026, 6, 1, tzinfo=UTC), title="Mid"),
        _ev(external_id="far", start_dt=datetime(2027, 1, 1, tzinfo=UTC), title="Far"),
    ])
    titles = {e.title for e in db.search(
        conn,
        start_after=datetime(2026, 1, 1, tzinfo=UTC),
        start_before=datetime(2026, 12, 31, tzinfo=UTC),
    )}
    assert titles == {"Mid"}


def test_search_results_ordered_by_start_dt(conn):
    db.upsert_events(conn, [
        _ev(external_id="c", start_dt=datetime(2026, 7, 1, tzinfo=UTC), title="Third"),
        _ev(external_id="a", start_dt=datetime(2026, 6, 1, tzinfo=UTC), title="First"),
        _ev(external_id="b", start_dt=datetime(2026, 6, 15, tzinfo=UTC), title="Second"),
    ])
    titles = [e.title for e in db.search(conn)]
    assert titles == ["First", "Second", "Third"]


def test_search_limit_respected(conn):
    db.upsert_events(conn, [
        _ev(external_id=f"x{i}", start_dt=datetime(2026, 6, i + 1, tzinfo=UTC))
        for i in range(10)
    ])
    assert len(db.search(conn, limit=3)) == 3


# --- FTS5 partial / prefix matching --------------------------------------


def test_fts_partial_word_matches_via_prefix(conn):
    db.upsert_events(conn, [
        _ev(external_id="m", title="Queens Museum Family Day", description="Workshops."),
        _ev(external_id="n", title="Brooklyn Nature Hike", description="Outdoor walking."),
    ])
    titles = {e.title for e in db.search(conn, query="muse")}
    assert titles == {"Queens Museum Family Day"}


def test_fts_matches_multiple_fields(conn):
    db.upsert_events(conn, [
        _ev(external_id="v", title="Untitled", description="Wonderful kid-friendly garden tour."),
        _ev(external_id="t", title="Bike Class", description="Practice riding safely."),
    ])
    titles = {e.title for e in db.search(conn, query="garden")}
    assert "Untitled" in titles


def test_fts_query_with_filters_combined(conn):
    db.upsert_events(conn, [
        _ev(external_id="1", title="Brooklyn Museum Tour", borough=Borough.BROOKLYN),
        _ev(external_id="2", title="Manhattan Museum Tour", borough=Borough.MANHATTAN),
    ])
    results = db.search(conn, query="museum", borough="Brooklyn")
    assert len(results) == 1
    assert results[0].borough == Borough.BROOKLYN


# --- prune_stale ---------------------------------------------------------


def test_prune_stale_removes_only_past_events(conn):
    db.upsert_events(conn, [
        _ev(external_id="old", start_dt=datetime(2025, 1, 1, tzinfo=UTC),
            end_dt=datetime(2025, 1, 1, 12, tzinfo=UTC), title="Old"),
        _ev(external_id="recent", start_dt=datetime(2026, 6, 1, tzinfo=UTC),
            end_dt=datetime(2026, 6, 1, 12, tzinfo=UTC), title="Recent"),
        _ev(external_id="future", start_dt=datetime(2027, 1, 1, tzinfo=UTC),
            end_dt=datetime(2027, 1, 1, 12, tzinfo=UTC), title="Future"),
    ])
    pruned = db.prune_stale(conn, datetime(2026, 1, 1, tzinfo=UTC))
    assert pruned == 1
    remaining = {e.title for e in db.search(conn)}
    assert remaining == {"Recent", "Future"}


def test_prune_uses_end_dt_when_present_falls_back_to_start(conn):
    db.upsert_events(conn, [
        _ev(external_id="long", start_dt=datetime(2025, 1, 1, tzinfo=UTC),
            end_dt=datetime(2026, 12, 31, tzinfo=UTC), title="Long-running"),
        _ev(external_id="point", start_dt=datetime(2025, 12, 25, tzinfo=UTC),
            end_dt=None, title="Point-in-time past"),
    ])
    pruned = db.prune_stale(conn, datetime(2026, 6, 1, tzinfo=UTC))
    assert pruned == 1  # point-in-time pruned, long-running survives
    titles = {e.title for e in db.search(conn)}
    assert titles == {"Long-running"}


def test_prune_requires_tz_aware_cutoff(conn):
    with pytest.raises(ValueError):
        db.prune_stale(conn, datetime(2026, 1, 1))  # naive


# --- list_sources --------------------------------------------------------


def test_list_sources_reports_counts_and_freshness(conn):
    db.upsert_events(conn, [
        _ev(external_id="a", source="src_a"),
        _ev(external_id="b", source="src_a"),
        _ev(external_id="c", source="src_b"),
    ])
    rows = db.list_sources(conn)
    by_source = {r["source"]: r for r in rows}
    assert by_source["src_a"]["event_count"] == 2
    assert by_source["src_b"]["event_count"] == 1
    assert all(r["last_seen"] for r in rows)


# --- compute_id semantics -----------------------------------------------


def test_compute_id_excludes_start_dt_so_time_changes_update_in_place(conn):
    e1 = _ev(external_id="time-shift", start_dt=datetime(2026, 6, 15, 10, tzinfo=UTC))
    e2 = _ev(external_id="time-shift", start_dt=datetime(2026, 6, 15, 14, tzinfo=UTC))
    assert e1.id == e2.id
    db.upsert_events(conn, [e1])
    db.upsert_events(conn, [e2])
    rows = list(db.search(conn))
    assert len(rows) == 1
    assert rows[0].start_dt.hour == 14  # the revised time
