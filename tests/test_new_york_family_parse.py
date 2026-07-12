"""Parser tests for the New York Family (Schneps network) source.

Uses the captured fixture (tests/fixtures/new_york_family_sample.json — real
rows from the live API, including a Long Island row, a recurring shared-id
row, and a page>1 husk stub) and inline dicts. Does not make network calls —
the parser takes a dict.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.new_york_family import (
    _infer_tags,
    _next_slice_start,
    _parse_age_bands,
    _parse_row,
    _resolve_borough,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "new_york_family_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text())["events"]


def _by_id(event_id: int) -> dict:
    return next(r for r in _load_events() if r.get("id") == event_id)


def _row(**overrides) -> dict:
    """Minimal valid five-borough row for inline tests."""
    base = {
        "id": 99001,
        "title": "Family Craft Morning",
        "description": "<p>Drop-in crafts for kids and caregivers.</p>",
        "excerpt": "",
        "categories": ["Family", "Craft &amp; DIY", "Kids (5–8)"],
        "start_date": "2026-07-18 10:00:00",
        "end_date": "2026-07-18 12:00:00",
        "timezone": "America/New_York",
        "cost": "Free",
        "url": "https://events.newyorkfamily.com/event/family-craft-morning/",
        "venue": {
            "venue": "Some Brooklyn Venue",
            "city": "Brooklyn",
            "geo_lat": 40.6771,
            "geo_lng": -73.9901,
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path — real fixture rows
# ---------------------------------------------------------------------------


def test_brooklyn_free_event_parses():
    ev = _parse_row(_by_id(851611))
    assert ev is not None
    assert ev.title == "Free Canoeing on the Gowanus Canal"
    assert ev.venue_name == "Gowanus Dredgers Canoe Club Boathouse"
    assert ev.borough == Borough.BROOKLYN
    # 2026-07-18 00:00 America/New_York (EDT) == 04:00 UTC
    assert ev.start_dt == datetime(2026, 7, 18, 4, 0, tzinfo=UTC)
    assert ev.price == Price.FREE
    assert ev.lat == 40.6771323
    assert ev.lng == -73.9901435
    assert ev.neighborhood is None  # enrich pass codes it from lat/lng
    assert "family" in ev.tags
    assert "outdoors" in ev.tags
    assert "sports" in ev.tags
    # No age-band categories on this row.
    assert ev.age_min is None and ev.age_max is None


def test_manhattan_row_resolves_by_coordinates():
    ev = _parse_row(_by_id(830753))
    assert ev is not None
    assert ev.borough == Borough.MANHATTAN
    assert "art" in ev.tags


def test_paid_theater_row():
    ev = _parse_row(_by_id(853667))
    assert ev is not None
    assert ev.price == Price.PAID  # "Tickets start at $49"
    assert "theater" in ev.tags
    # 09:30 EDT == 13:30 UTC
    assert ev.start_dt == datetime(2026, 7, 18, 13, 30, tzinfo=UTC)


def test_recurring_occurrences_get_distinct_external_ids():
    # Occurrences share the parent's numeric id upstream — the start suffix is
    # what keeps two same-day showtimes from collapsing into one row.
    row = _by_id(853667)
    first = _parse_row(row)
    later = _parse_row({**row, "start_date": "2026-07-18 15:30:00"})
    assert first is not None and later is not None
    assert first.external_id != later.external_id
    assert first.external_id.startswith("853667:")
    assert first.id != later.id


def test_age_bands_map_to_age_min_max():
    ev = _parse_row(_by_id(862697))  # Baby & Toddler (0–2)
    assert ev is not None
    assert (ev.age_min, ev.age_max) == (0, 2)
    assert "best for kids" in ev.tags
    assert "music" in ev.tags

    ev5k = _parse_row(_by_id(842240))  # Kids (5–8) + Tweens + Teens, Bronx
    assert ev5k is not None
    assert (ev5k.age_min, ev5k.age_max) == (5, 18)
    assert ev5k.borough == Borough.BRONX
    assert ev5k.price == Price.PAID


def test_queens_neighborhood_as_city_resolves():
    ev = _parse_row(_by_id(848151))  # Springfield Gardens (Queens coords)
    assert ev is not None
    assert ev.borough == Borough.QUEENS
    assert "volunteer" in ev.tags


# ---------------------------------------------------------------------------
# Geography filter — the reason this source exists as more than a copy-adapt
# ---------------------------------------------------------------------------


def test_long_island_rows_are_dropped():
    assert _parse_row(_by_id(784737)) is None  # Huntington Station
    assert _parse_row(_by_id(859254)) is None  # East Meadow


def test_city_fallback_when_coordinates_missing():
    row = _row(venue={"venue": "Somewhere", "city": "Woodhaven"})
    ev = _parse_row(row)
    assert ev is not None
    assert ev.borough == Borough.QUEENS
    assert ev.lat is None and ev.lng is None


def test_unknown_city_without_coordinates_is_dropped():
    assert _parse_row(_row(venue={"venue": "Somewhere", "city": "Hoboken"})) is None
    assert _parse_row(_row(venue={})) is None


def test_resolve_borough_prefers_coordinates_over_city():
    # A Bronx-coordinate venue whose city string says "New York" must not
    # come back as Manhattan.
    venue = {"city": "New York", "geo_lat": 40.9045, "geo_lng": -73.8964}
    assert _resolve_borough(venue) == Borough.BRONX


# ---------------------------------------------------------------------------
# Husks, missing fields, safety-net filters
# ---------------------------------------------------------------------------


def test_husk_row_is_skipped():
    husk = next(r for r in _load_events() if not r.get("id"))
    assert set(husk) == {"start_date", "end_date"}
    assert _parse_row(husk) is None


def test_row_without_parseable_start_is_skipped():
    assert _parse_row(_row(start_date=None)) is None
    assert _parse_row(_row(start_date="soon")) is None


def test_adult_blocklist_is_a_safety_net():
    assert _parse_row(_row(title="Wine Crawl 21+ After Dark")) is None
    assert _parse_row(_row(description="<p>Adults only, please.</p>")) is None
    assert _parse_row(_row(title="Members Only Preview Day")) is None


def test_free_category_backstops_missing_cost():
    ev = _parse_row(_row(cost="", categories=["Family", "Free"]))
    assert ev is not None
    assert ev.price == Price.FREE

    ev_unknown = _parse_row(_row(cost="", categories=["Family"]))
    assert ev_unknown is not None
    assert ev_unknown.price == Price.UNKNOWN


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_age_bands_handles_entity_escaped_names():
    lo, hi = _parse_age_bands(["Baby & Toddler (0–2)", "Tweens (9–12)"])
    assert (lo, hi) == (0, 12)
    assert _parse_age_bands(["Family", "Free"]) == (None, None)


def test_infer_tags_teens_only_is_not_best_for_kids():
    tags = _infer_tags(["Family", "Teens (13–18)", "Music"])
    assert "best for kids" not in tags
    assert tags[0] == "family"
    assert "music" in tags


def test_next_slice_start_advances_to_latest_start():
    rows = [
        {"start_date": "2026-07-18 00:00:00"},
        {"start_date": "2026-07-18 10:00:00"},
        {"start_date": "2026-07-18 09:00:00"},
    ]
    cur = datetime(2026, 7, 18, 0, 0)
    assert _next_slice_start(rows, cur) == datetime(2026, 7, 18, 10, 0)


def test_next_slice_start_jumps_when_stuck():
    # Every visible row is ongoing (started at/before the cursor): +2h so the
    # within-day walk always terminates.
    rows = [{"start_date": "2026-07-18 00:00:00"}, {"start_date": "garbage"}]
    cur = datetime(2026, 7, 18, 10, 0)
    assert _next_slice_start(rows, cur) == datetime(2026, 7, 18, 12, 0)
