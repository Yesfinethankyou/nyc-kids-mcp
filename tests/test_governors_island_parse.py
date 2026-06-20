"""Parser tests for the Governors Island source.

Uses the captured fixture (tests/fixtures/governors_island_sample.json) and
inline dicts. No network calls — the parser takes a dict.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

from nyc_events.models import Borough, Price
from nyc_events.sources.governors_island import (
    NYC_TZ,
    _infer_tags,
    _is_kid_relevant,
    _parse_floating_dt,
    _parse_row,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "governors_island_sample.json"


def _load_rows() -> list[dict]:
    return json.loads(FIXTURE.read_text())["data"]


def _row(**overrides) -> dict:
    """Minimal valid kid-relevant things-to-do row for inline tests."""
    base = {
        "id": 100558,
        "title": "Family Craft Workshop",
        "body": "<p>A hands-on craft workshop for kids and families.</p>",
        "startDate": "2026-07-25T12:00:00.000000Z",
        "endDate": "2026-07-25T17:00:00.000000Z",
        "url": "https://www.govisland.com/things-to-do/events/family-craft-workshop",
        "locations": [{"locationName": "Nolan Park - Building 10A"}],
        "calendar": {"name": "Events"},
    }
    base.update(overrides)
    return base


def _fixture_row(row_id: int) -> dict:
    return next(r for r in _load_rows() if r["id"] == row_id)


# ---------------------------------------------------------------------------
# Kid-relevance filtering (inclusive + blocklist)
# ---------------------------------------------------------------------------


def test_default_includes_keywordless_kid_item():
    # "Slide Hill" carries no kid keyword — an allowlist would wrongly drop it.
    assert _is_kid_relevant(_fixture_row(61360)) is True  # Slide Hill


def test_gala_dropped():
    assert _is_kid_relevant(_fixture_row(134336)) is False  # Governors Island Gala
    assert _parse_row(_fixture_row(134336)) is None


def test_road_races_dropped():
    for race_id in (61382, 61396, 71620):  # the three NYCRUNS 10Ks
        assert _is_kid_relevant(_fixture_row(race_id)) is False
        assert _parse_row(_fixture_row(race_id)) is None


def test_bike_rental_amenity_dropped():
    assert _is_kid_relevant(_fixture_row(61358)) is False  # Blazing Saddles Bike Rentals
    assert _parse_row(_fixture_row(61358)) is None


def test_hard_exclude_adult_signal():
    row = _row(title="Evening Social", body="<p>An adults only mixer, 21+.</p>")
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_race_regex_does_not_match_innocent_titles():
    assert _is_kid_relevant(_row(title="Pre-K Story Hour")) is True
    assert _is_kid_relevant(_row(title="Walk the Lavender Field")) is True


def test_title_blocklist_not_triggered_by_body_mention():
    # A festival whose body mentions "wine garden" must NOT be dropped — the
    # alcohol/gala terms are title-only.
    row = _row(title="Summer Food Festival", body="<p>Includes a wine garden.</p>")
    assert _is_kid_relevant(row) is True


# ---------------------------------------------------------------------------
# Floating-local datetime handling
# ---------------------------------------------------------------------------


def test_floating_z_is_parsed_as_local_not_utc():
    # "12:00:00Z" with openTimeText "12-5PM" means NOON local, not UTC.
    dt = _parse_floating_dt("2026-07-25T12:00:00.000000Z")
    assert dt == datetime(2026, 7, 25, 12, 0, 0, tzinfo=NYC_TZ)
    assert dt.tzinfo == ZoneInfo("America/New_York")
    # Same wall-clock as upstream's "12PM", offset is EDT (-4h), so 16:00 UTC.
    assert dt.utctimetuple().tm_hour == 16


def test_parse_floating_dt_handles_none_and_garbage():
    assert _parse_floating_dt(None) is None
    assert _parse_floating_dt("not-a-date") is None


# ---------------------------------------------------------------------------
# Happy-path row parse (fixture)
# ---------------------------------------------------------------------------


def test_happy_path_jazz_by_the_water():
    ev = _parse_row(_fixture_row(100558))
    assert ev is not None
    assert ev.title == "Jazz by the Water™"
    assert ev.source == "governors_island"
    assert ev.external_id == "100558"
    assert ev.start_dt == datetime(2026, 7, 25, 12, 0, 0, tzinfo=NYC_TZ)
    assert ev.end_dt == datetime(2026, 7, 25, 17, 0, 0, tzinfo=NYC_TZ)
    assert ev.venue_name == "Governors Island"
    assert ev.borough == Borough.MANHATTAN
    assert ev.lat is None
    assert ev.lng is None
    assert ev.age_min is None
    assert ev.age_max is None
    assert ev.price == Price.UNKNOWN
    assert ev.url == "https://www.govisland.com/things-to-do/events/jazz-by-the-water"
    assert "family" in ev.tags
    assert "music" in ev.tags
    assert ev.description is not None


def test_play_area_tagged_best_for_kids():
    ev = _parse_row(_fixture_row(61364))  # Hammock Grove Play Area
    assert ev is not None
    assert "best for kids" in ev.tags


# ---------------------------------------------------------------------------
# Fixture-wide invariants
# ---------------------------------------------------------------------------


def test_fixture_kept_titles():
    # Of the 17-row slice, the 3 NYCRUNS races, the gala, and the bike-rental
    # amenity are dropped; the remaining 12 family items are kept.
    kept = [ev for ev in (_parse_row(r) for r in _load_rows()) if ev is not None]
    titles = sorted(ev.title for ev in kept)
    assert titles == [
        "American Indian Community House",
        "Billion Oyster Project",
        "Cabin",
        "Gridlock on Governors Island",
        "Hammock Grove",
        "Hammock Grove Play Area",
        "Harvestworks",
        "Jazz by the Water™",
        "Misipasta x Governors Island",
        "Porch Stomp",
        "Slide Hill",
        "Yankee Hanger",
    ]


def test_all_kept_events_are_manhattan_unknown_price():
    for row in _load_rows():
        ev = _parse_row(row)
        if ev is None:
            continue
        assert ev.venue_name == "Governors Island"
        assert ev.borough == Borough.MANHATTAN
        assert ev.price == Price.UNKNOWN


def test_start_dt_is_tz_aware():
    for row in _load_rows():
        ev = _parse_row(row)
        if ev is None:
            continue
        assert ev.start_dt.tzinfo is not None


def test_event_ids_are_unique():
    parsed = [_parse_row(r) for r in _load_rows()]
    ids = [ev.id for ev in parsed if ev is not None]
    assert ids, "fixture should yield at least one kid-relevant event"
    assert len(ids) == len(set(ids)), "Duplicate event IDs"


def test_raw_payload_is_full_upstream_json():
    row = _fixture_row(100558)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.raw_payload is not None
    parsed = json.loads(ev.raw_payload)
    assert parsed["id"] == row["id"]
    assert parsed["slug"] == row["slug"]


# ---------------------------------------------------------------------------
# Missing optional fields
# ---------------------------------------------------------------------------


def test_missing_end_date_parses_without_error():
    ev = _parse_row(_row(endDate=None))
    assert ev is not None
    assert ev.end_dt is None


def test_missing_url_parses_without_error():
    ev = _parse_row(_row(url=None))
    assert ev is not None
    assert ev.url is None


def test_missing_start_date_returns_none():
    assert _parse_row(_row(startDate=None)) is None


def test_missing_title_returns_none():
    assert _parse_row(_row(title="")) is None


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------


def test_infer_tags_always_includes_family():
    assert "family" in _infer_tags("Some Event", "")


def test_infer_tags_music_and_outdoors():
    tags = _infer_tags("Jazz by the Water", "outdoor concert in the garden")
    assert "music" in tags
    assert "outdoors" in tags
