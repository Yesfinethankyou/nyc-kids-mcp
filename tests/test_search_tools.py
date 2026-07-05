"""Tool-level tests for the search_events date-range window + facet discovery.

These exercise the MCP tool functions directly (FastMCP's @tool decorator
returns the original callable) against a temp DB, monkeypatching
config.DB_PATH. They cover the window math layered on top of db.search —
explicit start_date/end_date ranges, the days_ahead width fallback, and the
end-before-start guard — plus that the new filters thread through.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nyc_events import config, db, tools
from nyc_events.models import Borough, Event, Price, compute_id

NYC_TZ = tools.NYC_TZ


def _ev(**overrides):
    base = dict(
        source="testsrc",
        title="An Event",
        description="desc",
        url="https://example.com/e",
        # noon NYC local so the stored UTC instant stays on the same calendar
        # date the window math compares against.
        start_dt=datetime(2026, 8, 14, 12, 0, tzinfo=NYC_TZ),
        venue_name="Somewhere",
        borough=Borough.BROOKLYN,
        price=Price.UNKNOWN,
        tags=[],
    )
    base.update(overrides)
    if "id" not in base:
        base["id"] = compute_id(base["source"], external_id=base["title"])
    return Event(**base)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = str(tmp_path / "events.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_events(path)
    with db.connect_events(path) as conn:
        db.upsert_events(conn, [
            _ev(external_id="before", title="Before",
                start_dt=datetime(2026, 8, 12, 12, tzinfo=NYC_TZ)),
            _ev(external_id="inrange", title="In range",
                start_dt=datetime(2026, 8, 14, 12, tzinfo=NYC_TZ)),
            _ev(external_id="after", title="After",
                start_dt=datetime(2026, 8, 16, 12, tzinfo=NYC_TZ)),
        ])
    return path


def test_explicit_date_range_bounds_inclusive(seeded_db):
    titles = {e["title"] for e in tools.search_events(
        start_date="2026-08-13", end_date="2026-08-15"
    )}
    assert titles == {"In range"}


def test_start_date_with_days_ahead_width(seeded_db):
    # start_date set, end_date omitted -> window is [start 00:00, start + N*24h],
    # same precise-instant semantics as the default now + days_ahead path.
    # 2 days from Aug 13 00:00 reaches Aug 15 00:00, catching the Aug 14 event
    # while still excluding Aug 12 (before) and Aug 16 (after).
    titles = {e["title"] for e in tools.search_events(
        start_date="2026-08-13", days_ahead=2
    )}
    assert titles == {"In range"}
    # 1 day ends Aug 14 00:00 — too narrow to reach the noon Aug 14 event.
    assert tools.search_events(start_date="2026-08-13", days_ahead=1) == []


def test_end_before_start_raises(seeded_db):
    with pytest.raises(ValueError):
        tools.search_events(start_date="2026-08-15", end_date="2026-08-13")


def test_bad_date_format_raises(seeded_db):
    with pytest.raises(ValueError):
        tools.search_events(start_date="08/13/2026")


def test_exclude_low_confidence_threads_through(tmp_path, monkeypatch):
    path = str(tmp_path / "events.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_events(path)
    with db.connect_events(path) as conn:
        db.upsert_events(conn, [
            _ev(external_id="perm", title="Permit", description=None, url=None),
            _ev(external_id="curated", title="Curated", description="real"),
        ])
    titles = {e["title"] for e in tools.search_events(
        start_date="2026-08-01", end_date="2026-08-31",
        exclude_low_confidence=True,
    )}
    assert titles == {"Curated"}


def test_list_facets_tool(tmp_path, monkeypatch):
    path = str(tmp_path / "events.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_events(path)
    with db.connect_events(path) as conn:
        db.upsert_events(conn, [
            _ev(external_id="a", source="src_a", neighborhood="Astoria",
                tags=["music"]),
        ])
    facets = tools.list_facets()
    assert facets["sources"] == ["src_a"]
    assert facets["neighborhoods"] == ["Astoria"]
    assert facets["tags"] == ["music"]
