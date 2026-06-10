"""Parser tests for the New York Transit Museum source.

Uses the captured fixture (tests/fixtures/ny_transit_museum_sample.json)
and inline dicts. Does not make network calls.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.ny_transit_museum import (
    _infer_tags,
    _is_kid_relevant,
    _parse_cost,
    _parse_row,
    _venue_fields,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ny_transit_museum_sample.json"

_MUSEUM_VENUE = {
    "id": 85,
    "venue": "New York Transit Museum, Brooklyn",
    "address": "Corner of Boerum Place & Schermerhorn Street",
    "city": "Brooklyn",
    "geo_lat": 40.6903327,
    "geo_lng": -73.9896449,
}


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text())["events"]


def _row(**overrides) -> dict:
    """Minimal valid kid-relevant Tribe row for inline tests."""
    base = {
        "id": 94346,
        "title": "Transit Tots",
        "description": "",
        "excerpt": "<p>Story-time, crafts, imaginative play.</p>",
        "categories": [{"name": "Family Programs"}],
        "utc_start_date": "2026-06-14 13:30:00",
        "utc_end_date": "2026-06-14 14:30:00",
        "cost": "$40",
        "url": "https://www.nytransitmuseum.org/program/transit-tots-14jun/",
        "venue": dict(_MUSEUM_VENUE),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Cost mapping
# ---------------------------------------------------------------------------


def test_parse_cost_dollar_amount_is_paid():
    assert _parse_cost("$40") == Price.PAID


def test_parse_cost_range_is_paid():
    assert _parse_cost("$10 – $20") == Price.PAID


def test_parse_cost_free():
    assert _parse_cost("Free") == Price.FREE


def test_parse_cost_included_with_admission_is_paid():
    assert _parse_cost("Included with Museum admission") == Price.PAID


def test_parse_cost_empty_is_unknown():
    assert _parse_cost("") == Price.UNKNOWN
    assert _parse_cost(None) == Price.UNKNOWN


# ---------------------------------------------------------------------------
# Category filtering
# ---------------------------------------------------------------------------


def test_family_programs_category_passes():
    assert _is_kid_relevant(_row(categories=[{"name": "Family Programs"}])) is True


def test_nostalgia_rides_category_passes():
    assert _is_kid_relevant(_row(categories=[{"name": "Nostalgia Rides"}])) is True


def test_adult_tour_filtered_out():
    row = _row(
        title="Transit Walk: Downtown Brooklyn",
        categories=[{"name": "Public Programs"}, {"name": "Tours"}],
    )
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_no_categories_filtered_out():
    # e.g. live "Subway Simulator Sunday" row ships with categories=[]
    assert _is_kid_relevant(_row(categories=[])) is False


def test_members_only_category_excludes_even_with_family_programs():
    # Exclusion must win over allowlist overlap.
    row = _row(
        categories=[{"name": "Family Programs"}, {"name": "Members-Only Programs"}],
    )
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_virtual_programs_category_excludes_even_with_family_programs():
    row = _row(
        categories=[{"name": "Family Programs"}, {"name": "Virtual Programs"}],
    )
    assert _is_kid_relevant(row) is False


def test_hard_exclude_title_overrides_included_category():
    row = _row(title="Nostalgia Ride (21+)", categories=[{"name": "Nostalgia Rides"}])
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_fixture_members_only_virtual_and_adult_tours_filtered_out():
    # 93098 Jewel in the Crown (Members-Only), 94428 Curator Talk (Virtual),
    # 93034 Transit Walk (adult tour), 94436 Subway Simulator Sunday (no
    # categories), 94409 Special Day (Access Programs — outside allowlist).
    dropped_ids = {93098, 94428, 93034, 94436, 94409}
    seen = set()
    for row in _load_events():
        if row["id"] in dropped_ids:
            seen.add(row["id"])
            assert _parse_row(row) is None, f"{row['title']} should be filtered out"
    assert seen == dropped_ids


# ---------------------------------------------------------------------------
# Venue / borough mapping
# ---------------------------------------------------------------------------


def test_museum_venue_maps_to_brooklyn_with_geo():
    name, borough, lat, lng = _venue_fields(_row())
    assert name == "New York Transit Museum, Brooklyn"
    assert borough == Borough.BROOKLYN
    assert abs(lat - 40.6903327) < 1e-6
    assert abs(lng - -73.9896449) < 1e-6


def test_offsite_venue_has_no_borough_or_geo():
    row = _row(
        venue={"id": 92334, "venue": "Off-Site", "city": None, "geo_lat": None, "geo_lng": None},
    )
    name, borough, lat, lng = _venue_fields(row)
    assert name == "Off-Site"
    assert borough is None
    assert lat is None
    assert lng is None


def test_missing_venue_object_parses_without_error():
    ev = _parse_row(_row(venue=[]))
    assert ev is not None
    assert ev.venue_name is None
    assert ev.borough is None


# ---------------------------------------------------------------------------
# Happy-path row parse (fixture)
# ---------------------------------------------------------------------------


def test_happy_path_transit_tots():
    row = next(e for e in _load_events() if e["id"] == 94346)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "Transit Tots"
    assert ev.source == "ny_transit_museum"
    assert ev.external_id == "94346"
    assert ev.start_dt == datetime(2026, 6, 14, 13, 30, 0, tzinfo=UTC)
    assert ev.end_dt == datetime(2026, 6, 14, 14, 30, 0, tzinfo=UTC)
    assert ev.venue_name == "New York Transit Museum, Brooklyn"
    assert ev.borough == Borough.BROOKLYN
    assert ev.lat is not None
    assert ev.lng is not None
    assert ev.price == Price.PAID
    assert ev.url == "https://www.nytransitmuseum.org/program/transit-tots-14jun/"
    assert "family" in ev.tags
    assert "best for kids" in ev.tags
    assert ev.description is not None
    assert "story-time" in ev.description.lower()


def test_family_workshop_paid_range():
    row = next(e for e in _load_events() if e["id"] == 92415)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "Movers and Makers: Family Tour and Workshop"
    assert ev.price == Price.PAID
    assert "educational" in ev.tags


def test_nostalgia_ride_included_with_admission():
    row = next(e for e in _load_events() if e["id"] == 10013796)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "50th Anniversary Weekend Shuttle Rides"
    assert ev.price == Price.PAID
    assert "trains" in ev.tags
    assert ev.borough == Borough.BROOKLYN


def test_recurring_occurrences_get_distinct_ids():
    # Verified live (2026-06-10): the Tribe id is per-occurrence. 94346 /
    # 94344 are two Transit Tots Sundays; each must become its own Event.
    events = [e for e in _load_events() if e["id"] in (94346, 94344)]
    assert len(events) == 2
    parsed = [_parse_row(e) for e in events]
    assert all(ev is not None for ev in parsed)
    assert parsed[0].external_id != parsed[1].external_id
    assert parsed[0].id != parsed[1].id
    assert parsed[0].title == parsed[1].title


def test_start_dt_is_utc_aware():
    for row in _load_events():
        ev = _parse_row(row)
        if ev is None:
            continue
        assert ev.start_dt.tzinfo is not None
        assert ev.start_dt.utcoffset().total_seconds() == 0


def test_event_ids_are_unique():
    parsed = [_parse_row(e) for e in _load_events()]
    ids = [ev.id for ev in parsed if ev is not None]
    assert ids, "fixture should yield at least one kid-relevant event"
    assert len(ids) == len(set(ids)), "Duplicate event IDs"


# ---------------------------------------------------------------------------
# Missing optional fields
# ---------------------------------------------------------------------------


def test_missing_end_date_parses_without_error():
    ev = _parse_row(_row(utc_end_date=None))
    assert ev is not None
    assert ev.end_dt is None


def test_missing_url_parses_without_error():
    ev = _parse_row(_row(url=None))
    assert ev is not None
    assert ev.url is None


def test_missing_cost_parses_as_unknown():
    ev = _parse_row(_row(cost=None))
    assert ev is not None
    assert ev.price == Price.UNKNOWN


def test_missing_start_date_returns_none():
    assert _parse_row(_row(utc_start_date=None)) is None


def test_missing_title_returns_none():
    assert _parse_row(_row(title="")) is None


def test_empty_description_falls_back_to_excerpt():
    # Live rows ship description="" with the text in excerpt.
    ev = _parse_row(_row(description="", excerpt="<p>Crafts and imaginative play.</p>"))
    assert ev is not None
    assert ev.description == "Crafts and imaginative play."


# ---------------------------------------------------------------------------
# Raw payload
# ---------------------------------------------------------------------------


def test_raw_payload_is_full_upstream_json():
    row = next(e for e in _load_events() if e["id"] == 94346)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.raw_payload is not None
    parsed = json.loads(ev.raw_payload)
    assert parsed["id"] == row["id"]
    assert parsed["categories"] == row["categories"]
    assert parsed["venue"]["venue"] == "New York Transit Museum, Brooklyn"


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------


def test_infer_tags_always_includes_family():
    assert "family" in _infer_tags("Some Event", set())


def test_infer_tags_from_categories():
    tags = _infer_tags("Shuttle Rides", {"Nostalgia Rides"})
    assert "trains" in tags


def test_infer_tags_from_title_keywords():
    tags = _infer_tags("Subway Story Time for Tots", set())
    assert "trains" in tags
    assert "educational" in tags
    assert "best for kids" in tags
