"""DB integration tests.

Covers what Phase 1 promised:
- upsert is idempotent (same input twice -> no duplicates, last_seen updated)
- search filters: borough, age window, free_only, date range
- FTS5 partial-word matching ("muse" matches "Museum")
- prune_stale removes only past events
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

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
    path = str(tmp_path / "test.db")
    db.init_events(path)
    with db.connect_events(path) as c:
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


# --- upsert vs enrichment (neighborhood / lat / lng persistence) ----------
#
# Sources yield neighborhood=None (and usually lat/lng=None); enrich.py fills
# them in a second pass. The upsert must not blank those enriched values on
# the nightly re-ingest — otherwise one failed enrich pass leaves the whole
# catalog without neighborhoods for a day.


def _enrich_row(conn, event_id, *, neighborhood="Williamsburg", lat=40.71, lng=-73.96):
    """Simulate what enrich.run writes for a coded row."""
    conn.execute(
        "UPDATE events SET neighborhood = ?, lat = COALESCE(lat, ?), "
        "lng = COALESCE(lng, ?) WHERE id = ?",
        (neighborhood, lat, lng, event_id),
    )
    conn.commit()


def test_upsert_preserves_enriched_fields_on_reingest(conn):
    e = _ev(external_id="e1", neighborhood=None, lat=None, lng=None)
    db.upsert_events(conn, [e])
    _enrich_row(conn, e.id)
    db.upsert_events(conn, [e])  # nightly re-ingest, source fields unchanged
    row = db.get_event_by_id(conn, e.id)
    assert row.neighborhood == "Williamsburg"
    assert (row.lat, row.lng) == (40.71, -73.96)


def test_upsert_source_provided_location_wins_over_enrichment(conn):
    e = _ev(external_id="e1", neighborhood=None, lat=None, lng=None)
    db.upsert_events(conn, [e])
    _enrich_row(conn, e.id)
    # Source starts providing its own neighborhood + coords: they win.
    db.upsert_events(conn, [_ev(
        external_id="e1", neighborhood="DUMBO", lat=40.70, lng=-73.99,
    )])
    row = db.get_event_by_id(conn, e.id)
    assert row.neighborhood == "DUMBO"
    assert (row.lat, row.lng) == (40.70, -73.99)


def test_upsert_venue_change_resets_enrichment(conn):
    e = _ev(external_id="e1", neighborhood=None, lat=None, lng=None)
    db.upsert_events(conn, [e])
    _enrich_row(conn, e.id)
    # Upstream moves the event to a different venue (same external_id, so the
    # same row): the stale coding must reset so enrich re-resolves it tonight.
    db.upsert_events(conn, [_ev(
        external_id="e1", venue_name="McCarren Park",
        neighborhood=None, lat=None, lng=None,
    )])
    row = db.get_event_by_id(conn, e.id)
    assert row.neighborhood is None
    assert (row.lat, row.lng) == (None, None)


def test_upsert_borough_change_resets_enrichment(conn):
    e = _ev(external_id="e1", neighborhood=None, lat=None, lng=None)
    db.upsert_events(conn, [e])
    _enrich_row(conn, e.id)
    db.upsert_events(conn, [_ev(
        external_id="e1", borough=Borough.QUEENS,
        neighborhood=None, lat=None, lng=None,
    )])
    row = db.get_event_by_id(conn, e.id)
    assert row.neighborhood is None
    assert (row.lat, row.lng) == (None, None)


# --- search filters ------------------------------------------------------


def test_search_filters_by_borough(conn):
    db.upsert_events(conn, [
        _ev(external_id="b1", borough=Borough.BROOKLYN, title="Brooklyn event"),
        _ev(external_id="m1", borough=Borough.MANHATTAN, title="Manhattan event"),
    ])
    results = db.search(conn, borough="Brooklyn")
    assert len(results) == 1 and results[0].borough == Borough.BROOKLYN


def test_search_filters_by_borough_list(conn):
    db.upsert_events(conn, [
        _ev(external_id="b1", borough=Borough.BROOKLYN, title="Brooklyn event"),
        _ev(external_id="m1", borough=Borough.MANHATTAN, title="Manhattan event"),
        _ev(external_id="q1", borough=Borough.QUEENS, title="Queens event"),
    ])
    titles = {e.title for e in db.search(conn, borough=["Brooklyn", "Queens"])}
    assert titles == {"Brooklyn event", "Queens event"}
    # A single-element list still works (not just the str fast path).
    assert {e.title for e in db.search(conn, borough=["Manhattan"])} == {"Manhattan event"}
    # An empty list is "no filter", same as None — not "match nothing".
    assert len(db.search(conn, borough=[])) == 3


def test_search_filters_by_source_list(conn):
    db.upsert_events(conn, [
        _ev(external_id="a", source="src_a", title="From A"),
        _ev(external_id="b", source="src_b", title="From B"),
        _ev(external_id="c", source="src_c", title="From C"),
    ])
    titles = {e.title for e in db.search(conn, source=["src_a", "src_c"])}
    assert titles == {"From A", "From C"}


def test_search_filters_by_neighborhood_list_is_exact_not_substring(conn):
    db.upsert_events(conn, [
        _ev(external_id="n1", neighborhood="Crown Heights (North)", title="North CH"),
        _ev(external_id="n2", neighborhood="Crown Heights (South)", title="South CH"),
        _ev(external_id="n3", neighborhood="Williamsburg", title="Wburg"),
    ])
    # Unlike the str form, a list matches literal values only — no substring
    # expansion (the dashboard's multi-select options are exact facet values).
    titles = {
        e.title
        for e in db.search(conn, neighborhood=["Crown Heights (North)", "Williamsburg"])
    }
    assert titles == {"North CH", "Wburg"}
    assert db.search(conn, neighborhood=["Crown Heights"]) == []  # no exact match


def test_search_filters_by_neighborhood_substring(conn):
    db.upsert_events(conn, [
        _ev(external_id="n1", neighborhood="Crown Heights (North)", title="North CH"),
        _ev(external_id="n2", neighborhood="Crown Heights (South)", title="South CH"),
        _ev(external_id="n3", neighborhood="Williamsburg", title="Wburg"),
    ])
    # Colloquial prefix matches both official NTA variants, case-insensitively.
    titles = {e.title for e in db.search(conn, neighborhood="crown heights")}
    assert titles == {"North CH", "South CH"}
    assert {e.title for e in db.search(conn, neighborhood="Williamsburg")} == {"Wburg"}


def test_search_neighborhood_filter_escapes_wildcards(conn):
    db.upsert_events(conn, [
        _ev(external_id="r1", neighborhood="Crown Heights", title="Real"),
    ])
    # A literal '%' must not match everything.
    assert db.search(conn, neighborhood="%") == []


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


def test_search_filters_by_source(conn):
    db.upsert_events(conn, [
        _ev(external_id="a", source="src_a", title="From A"),
        _ev(external_id="b", source="src_b", title="From B"),
    ])
    assert {e.title for e in db.search(conn, source="src_a")} == {"From A"}


def test_search_exclude_low_confidence(conn):
    # low_confidence == description IS NULL AND url IS NULL (permit-style rows).
    db.upsert_events(conn, [
        _ev(external_id="perm", description=None, url=None, title="Permit row"),
        _ev(external_id="desc", description="has detail", url=None, title="Has desc"),
        _ev(external_id="link", description=None, url="https://x/y", title="Has url"),
        _ev(external_id="full", description="d", url="https://x/z", title="Has both"),
    ])
    titles = {e.title for e in db.search(conn, exclude_low_confidence=True)}
    assert "Permit row" not in titles
    assert titles == {"Has desc", "Has url", "Has both"}


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


def test_whitespace_only_query_does_not_crash(conn):
    # A whitespace-only query tokenizes to no FTS terms; it must degrade to a
    # text-unfiltered search rather than raising an FTS5 syntax error (issue #61).
    db.upsert_events(conn, [
        _ev(external_id="a", title="Storytime"),
        _ev(external_id="b", title="Nature Walk"),
    ])
    assert len(db.search(conn, query="   ")) == 2
    assert len(db.search(conn, query="\n\t")) == 2


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


# --- list_facets ---------------------------------------------------------


def test_list_facets_returns_distinct_sorted_values(conn):
    db.upsert_events(conn, [
        _ev(external_id="a", source="src_a", borough=Borough.BROOKLYN,
            neighborhood="Williamsburg", tags=["music", "family"]),
        _ev(external_id="b", source="src_b", borough=Borough.QUEENS,
            neighborhood="Astoria", tags=["family", "art"]),
        # Duplicate borough/neighborhood/tags must collapse to one entry each.
        _ev(external_id="c", source="src_a", borough=Borough.BROOKLYN,
            neighborhood="Williamsburg", tags=["music"]),
    ])
    facets = db.list_facets(conn)
    assert facets["boroughs"] == ["Brooklyn", "Queens"]
    assert facets["neighborhoods"] == ["Astoria", "Williamsburg"]
    assert facets["tags"] == ["art", "family", "music"]
    assert facets["sources"] == ["src_a", "src_b"]


def test_list_facets_skips_null_borough_and_neighborhood(conn):
    db.upsert_events(conn, [
        _ev(external_id="n", borough=None, neighborhood=None, tags=[]),
    ])
    facets = db.list_facets(conn)
    assert facets["boroughs"] == []
    assert facets["neighborhoods"] == []
    assert facets["tags"] == []


# --- geocode cache -------------------------------------------------------


def test_geocode_cache_roundtrip(conn):
    assert db.get_geocode(conn, "fwd:domino park|brooklyn") is None  # miss
    db.put_geocode(conn, "fwd:domino park|brooklyn", 40.71, -73.96, "Williamsburg")
    assert db.get_geocode(conn, "fwd:domino park|brooklyn") == (40.71, -73.96, "Williamsburg")


def test_geocode_cache_remembers_negative_result(conn):
    # A stored all-NULL row is a hit (a remembered miss), distinct from None.
    db.put_geocode(conn, "fwd:nowhere|queens", None, None, None)
    assert db.get_geocode(conn, "fwd:nowhere|queens") == (None, None, None)


def test_geocode_cache_upserts_on_conflict(conn):
    db.put_geocode(conn, "rev:1,2", 1.0, 2.0, "Old")
    db.put_geocode(conn, "rev:1,2", 1.0, 2.0, "New")
    assert db.get_geocode(conn, "rev:1,2") == (1.0, 2.0, "New")


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


# --- source_health / catalog_stats (dashboard queries) --------------------

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def test_source_health_zero_row_registered_source_still_appears(conn):
    db.upsert_events(conn, [_ev(external_id="e1", source="srcA")])
    rows = db.source_health(conn, _NOW, registered=["srcA", "srcB"])
    by_source = {r["source"]: r for r in rows}
    assert by_source["srcB"]["event_count"] == 0
    assert by_source["srcB"]["registered"] is True
    assert by_source["srcB"]["last_seen"] is None
    assert by_source["srcA"]["event_count"] == 1


def test_source_health_counts(conn):
    db.upsert_events(
        conn,
        [
            # future, curated
            _ev(external_id="f1", source="s", start_dt=_NOW + timedelta(days=1)),
            # past
            _ev(external_id="p1", source="s", start_dt=_NOW - timedelta(days=1)),
            # future, low-confidence (no description, no url)
            _ev(
                external_id="lc1",
                source="s",
                start_dt=_NOW + timedelta(days=2),
                description=None,
                url=None,
            ),
        ],
    )
    # One row flagged missing long enough ago to count; one too recently.
    old = (_NOW - timedelta(hours=40)).isoformat()
    fresh = (_NOW - timedelta(hours=2)).isoformat()
    ids = [r["id"] for r in conn.execute("SELECT id FROM events ORDER BY start_dt")]
    conn.execute("UPDATE events SET missing_since = ? WHERE id = ?", (old, ids[0]))
    conn.execute("UPDATE events SET missing_since = ? WHERE id = ?", (fresh, ids[1]))
    conn.commit()
    (row,) = db.source_health(conn, _NOW, registered=["s"])
    assert row["event_count"] == 3
    assert row["future_count"] == 2
    assert row["low_confidence"] == 1
    assert row["flagged_missing"] == 1  # only the 40h-old stamp; 2h is in grace


def test_source_health_unregistered_source_with_rows_appears(conn):
    db.upsert_events(conn, [_ev(external_id="e1", source="ghost")])
    rows = db.source_health(conn, _NOW, registered=[])
    assert rows[0]["source"] == "ghost"
    assert rows[0]["registered"] is False


def test_source_health_joins_latest_ingest_run(conn):
    db.upsert_events(conn, [_ev(external_id="e1", source="s")])
    for i, outcome in enumerate(["ok", "fetch_failed"]):
        db.record_ingest_run(
            conn,
            run_id=f"r{i}",
            source="s",
            started_at=_NOW - timedelta(hours=2 - i),
            finished_at=_NOW - timedelta(hours=2 - i, minutes=-5),
            outcome=outcome,
            fetched=10 + i,
            inserted=0,
            updated=0,
            marked_missing=0,
        )
    (row,) = db.source_health(conn, _NOW, registered=["s"])
    assert row["last_run_outcome"] == "fetch_failed"  # the latest run wins
    assert row["last_run_fetched"] == 11


def test_source_health_requires_aware_now(conn):
    with pytest.raises(ValueError):
        db.source_health(conn, datetime(2026, 6, 20, 12, 0), registered=[])


def test_catalog_stats(conn):
    db.upsert_events(
        conn,
        [
            _ev(external_id="f1", start_dt=_NOW + timedelta(days=1)),
            _ev(external_id="p1", start_dt=_NOW - timedelta(days=1), neighborhood=None),
        ],
    )
    db.put_geocode(conn, "fwd:x|y", None, None, None)
    stats = db.catalog_stats(conn, _NOW)
    assert stats["total_events"] == 2
    assert stats["future_events"] == 1
    assert stats["with_neighborhood"] == 1
    assert stats["neighborhood_pct"] == 50.0
    assert stats["geocode_cache_rows"] == 1


def test_catalog_stats_empty_db(conn):
    stats = db.catalog_stats(conn, _NOW)
    assert stats["total_events"] == 0
    assert stats["neighborhood_pct"] == 0.0


def test_connect_events_ro_rejects_writes(tmp_path):
    path = str(tmp_path / "ro.db")
    db.init_events(path)
    with db.connect_events_ro(path) as conn:
        # reads work
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        # writes are physically impossible on a mode=ro connection
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO geocode_cache (lookup_key, resolved_at) VALUES ('x', 'y')")
