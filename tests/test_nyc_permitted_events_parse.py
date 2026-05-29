"""NYC Permitted Events parser tests.

Uses a real captured fixture (tests/fixtures/tvpp_9vvx_sample.json) plus
hand-crafted dicts for edge cases the live data doesn't always exhibit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from nyc_events.models import Borough, Price
from nyc_events.sources.nyc_permitted_events import (
    NYCPermittedEventsSource,
    _clean_row,
    _clean_venue,
    _infer_tags,
    _parse_local_dt,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tvpp_9vvx_sample.json"


def _row(**overrides):
    """Build a plausible SODA row with defaults, override fields as needed."""
    base = {
        "event_id": "999001",
        "event_name": "Family Storytime in the Park",
        "start_date_time": "2026-06-15T10:00:00.000",
        "end_date_time": "2026-06-15T11:00:00.000",
        "event_agency": "Parks Department",
        "event_type": "Special Event",
        "event_borough": "Brooklyn",
        "event_location": "Prospect Park: Picnic House   ,Brooklyn",
    }
    base.update(overrides)
    return base


def _parse(row):
    return NYCPermittedEventsSource()._parse_row(row)


# --- happy path ----------------------------------------------------------


def test_parses_a_good_row():
    ev = _parse(_row())
    assert ev is not None
    assert ev.title == "Family Storytime in the Park"
    assert ev.source == "nyc_permitted_events"
    # external_id binds permit_id:start_dt so recurring permits expand into
    # multiple DB rows (one per occurrence).
    assert ev.external_id.startswith("999001:")
    assert ev.borough == Borough.BROOKLYN
    assert ev.venue_name == "Prospect Park"  # colon-suffix + comma-trail stripped
    assert ev.price == Price.UNKNOWN
    assert "family" in ev.tags
    assert "story time" in ev.tags


def test_recurring_permit_yields_distinct_external_ids():
    # Same permit_id, different start_date_time -> distinct external_ids.
    a = _parse(_row(event_id="900022", start_date_time="2026-06-10T18:00:00.000"))
    b = _parse(_row(event_id="900022", start_date_time="2026-06-17T18:00:00.000"))
    assert a.external_id != b.external_id
    assert a.id != b.id


def test_stable_id_is_deterministic():
    a = _parse(_row())
    b = _parse(_row())
    assert a is not None and b is not None
    assert a.id == b.id


# --- type / agency filtering ---------------------------------------------


def test_sport_youth_is_filtered_out():
    # Sport-Youth rows are league field permits, not events for the public.
    assert _parse(_row(event_type="Sport - Youth")) is None


def test_parade_is_filtered_out():
    assert _parse(_row(event_type="Parade")) is None


def test_religious_event_type_is_filtered_out():
    assert _parse(_row(event_type="Religious Event")) is None


# --- title blocklist -----------------------------------------------------


@pytest.mark.parametrize("bad_title", [
    "Eid al-Adha",
    "EID PRAYER",
    "BRIC Celebrate Brooklyn 2026 Load-in and Load-Out",
    "Setup for Festival",
    "Construction at Site",
    "Radio Control Model Planes",
    "Model Helicopter Flying",
    "PS 152 Field Day",
    "PS-39 Carnival",
    "PS39 Spring Picnic",
    "I.S. 223 Running for Success Seventh Annual 5K Classic",
    "IS 318 Carnival",
    "JHS 50 Open House",
    "MS 322 Spring Concert",
    "HS 615 Family Day",
    "BWLS 12 Field Day",
    "BKG 7 Spring Festival",
    "K-1 Field Day - Elementary School",
    "Field Days at Crotona Park",
    "School Picnic Day",
    "Private Wedding Ceremony",
    "Field Reservation - Soccer 5",
    "Box Office Coordination",
    "Community Outreach Day",
])
def test_title_blocklist_filters(bad_title):
    assert _parse(_row(event_name=bad_title)) is None


@pytest.mark.parametrize("useless", ["Miscellaneous", "miscellaneous", "Celebration", "TBD", "N/A"])
def test_useless_titles_filtered(useless):
    assert _parse(_row(event_name=useless)) is None


def test_short_title_filtered():
    assert _parse(_row(event_name="ab")) is None


def test_empty_title_filtered():
    assert _parse(_row(event_name="")) is None
    assert _parse(_row(event_name=None)) is None


# --- borough inference ---------------------------------------------------


def test_borough_field_used_when_present():
    assert _parse(_row(event_borough="Bronx")).borough == Borough.BRONX
    assert _parse(_row(event_borough="Staten Island")).borough == Borough.STATEN_ISLAND
    assert _parse(_row(event_borough="THE BRONX")).borough == Borough.BRONX


def test_unknown_borough_left_as_none():
    assert _parse(_row(event_borough="New Jersey")).borough is None
    assert _parse(_row(event_borough="")).borough is None


# --- datetime parsing ----------------------------------------------------


def test_start_dt_is_localized_to_nyc():
    ev = _parse(_row(start_date_time="2026-07-04T14:30:00.000"))
    assert ev.start_dt.year == 2026
    assert ev.start_dt.month == 7
    assert ev.start_dt.day == 4
    assert ev.start_dt.tzinfo is not None
    # 2026-07-04 14:30 NYC local = 18:30 UTC (EDT, UTC-4)
    assert ev.start_dt.utcoffset().total_seconds() == -4 * 3600


def test_bad_end_dt_dropped_not_event():
    # tvpp-9vvx sometimes has end_date_time < start_date_time.
    ev = _parse(_row(start_date_time="2026-06-15T18:00:00.000",
                     end_date_time="2026-06-15T14:00:00.000"))
    assert ev is not None
    assert ev.start_dt is not None
    assert ev.end_dt is None


def test_unparseable_start_filters_row():
    assert _parse(_row(start_date_time="not a date")) is None
    assert _parse(_row(start_date_time=None)) is None


# --- venue cleaning ------------------------------------------------------


def test_clean_venue_strips_colon_suffix_and_comma():
    assert _clean_venue("Marine Park: Hobby Field   ,Brooklyn") == "Marine Park"
    assert _clean_venue("Prospect Park: Bandshell") == "Prospect Park"
    assert _clean_venue("Union Square") == "Union Square"
    assert _clean_venue("") == ""


# --- tag inference -------------------------------------------------------


def test_tags_include_story_time_for_storytime_title():
    tags = _infer_tags("Kensington Library Storytime", "Special Event")
    assert "story time" in tags


def test_tags_include_nature_for_garden_keyword():
    tags = _infer_tags("Community Garden Cleanup", "Special Event")
    assert "nature" in tags


def test_tags_can_be_empty_for_inscrutable_event():
    # _infer_tags returns [] when no keyword matches; the row gets dropped
    # at _parse_row time. _infer_tags itself is permissive — it just reports.
    assert _infer_tags("Out The Kitchen", "Special Event") == []


def test_untagged_row_is_filtered_at_parse():
    # An event title with no kid-friendly keyword — like a private picnic
    # permit — should be dropped entirely. Noise control.
    assert _parse(_row(event_name="Engagement Party")) is None
    assert _parse(_row(event_name="Press Conference")) is None
    assert _parse(_row(event_name="Barbecue")) is None


# --- per-row try/except (Source.fetch wrapper) ---------------------------


def test_one_bad_row_does_not_kill_iteration(monkeypatch):
    rows = [
        _row(event_id="ok-1", event_name="Family Music Day"),
        {"event_id": "bad", "event_type": "Special Event",
         "event_name": "Trigger Error", "event_borough": "Brooklyn",
         "start_date_time": "2026-06-15T10:00:00.000",
         # event_location missing entirely — _clean_venue handles, but let's
         # nuke _parse_row instead by patching it to raise for this id.
         "event_location": ""},
        _row(event_id="ok-2", event_name="Storytime in the Park"),
    ]
    src = NYCPermittedEventsSource()
    monkeypatch.setattr(src, "_fetch_rows", lambda: rows)
    original = src._parse_row

    def parse_or_die(row):
        if row.get("event_id") == "bad":
            raise RuntimeError("simulated parse failure")
        return original(row)

    monkeypatch.setattr(src, "_parse_row", parse_or_die)
    events = list(src.fetch())
    permit_prefixes = [e.external_id.split(":", 1)[0] for e in events]
    assert "ok-1" in permit_prefixes
    assert "ok-2" in permit_prefixes
    assert "bad" not in permit_prefixes


# --- _clean_row: upstream-junk cleaners ----------------------------------


def test_clean_row_end_before_start_dropped():
    row = {
        "event_id": "x",
        "event_name": "Soccer",
        "start_date_time": "2026-05-28T18:00:00.000",
        "end_date_time": "2026-05-28T14:00:00.000",
    }
    cleaned = _clean_row(row)
    assert cleaned["end_date_time"] is None
    # start untouched
    assert cleaned["start_date_time"] == "2026-05-28T18:00:00.000"


def test_clean_row_end_after_start_preserved():
    row = {
        "event_id": "x",
        "event_name": "Soccer",
        "start_date_time": "2026-05-28T14:00:00.000",
        "end_date_time": "2026-05-28T18:00:00.000",
    }
    cleaned = _clean_row(row)
    assert cleaned["end_date_time"] == "2026-05-28T18:00:00.000"


def test_clean_row_trailing_commas_stripped():
    row = {
        "event_id": "x",
        "event_name": "Storytime in the Park",
        "start_date_time": "2026-05-28T10:00:00.000",
        "community_board": "07,",
        "police_precinct": "44,",
    }
    cleaned = _clean_row(row)
    assert cleaned["community_board"] == "07"
    assert cleaned["police_precinct"] == "44"


def test_clean_row_leading_date_prefix_stripped():
    row = {
        "event_id": "x",
        "event_name": "2026.05.14 May evening horseshoecrab monitoring",
        "start_date_time": "2026-05-28T10:00:00.000",
    }
    cleaned = _clean_row(row)
    assert cleaned["event_name"] == "May evening horseshoecrab monitoring"


def test_clean_row_handles_single_digit_month_day():
    row = {
        "event_id": "x",
        "event_name": "2026.5.4 Spring Fest",
        "start_date_time": "2026-05-28T10:00:00.000",
    }
    cleaned = _clean_row(row)
    assert cleaned["event_name"] == "Spring Fest"


def test_clean_row_collapses_multiple_spaces():
    row = {
        "event_id": "x",
        "event_name": "Shakespeare Performance  -  Julius Caesar",
        "start_date_time": "2026-05-28T10:00:00.000",
    }
    cleaned = _clean_row(row)
    assert cleaned["event_name"] == "Shakespeare Performance - Julius Caesar"


def test_clean_row_handles_missing_fields():
    # Should not raise on rows with no community_board / police_precinct /
    # bad dates / missing name.
    _clean_row({})
    _clean_row({"event_id": "x"})
    _clean_row({"event_id": "x", "start_date_time": "not-a-date"})


def test_parse_row_applies_cleaners():
    # End-to-end: a row with junk fields should produce a clean Event.
    row = _row(
        event_name="2026.05.14 May   storytime in   the   park",
        start_date_time="2026-05-28T14:00:00.000",
        end_date_time="2026-05-28T10:00:00.000",
    )
    ev = _parse(row)
    assert ev is not None
    assert ev.title == "May storytime in the park"
    assert ev.end_dt is None  # bad end dropped


# --- rain-date dedup -----------------------------------------------------


def test_rain_date_occurrence_dropped_when_date_in_title_matches_start(monkeypatch):
    rows = [
        # Permit-99: title names May 16 primary + May 30 rain date.
        # May 16 row should survive; May 30 row is the rain-day backup -> drop.
        _row(event_id="permit-99",
             event_name="Brooklyn Health Fair May 16 and Rain Date May 30",
             start_date_time="2026-05-16T10:00:00.000"),
        _row(event_id="permit-99",
             event_name="Brooklyn Health Fair May 16 and Rain Date May 30",
             start_date_time="2026-05-30T10:00:00.000"),
        # Permit-50: regular recurring permit, no rain date -> both kept.
        _row(event_id="permit-50",
             event_name="Storytime at the Park",
             start_date_time="2026-06-01T10:00:00.000"),
        _row(event_id="permit-50",
             event_name="Storytime at the Park",
             start_date_time="2026-06-08T10:00:00.000"),
        # Permit-77: Juneteenth movie night with rain-date hedge for next
        # day. The primary occurrence (June 19) does NOT match the rain
        # date string ("6/20/2026") -> keep.
        _row(event_id="permit-77",
             event_name="Juneteenth Movie Night RAIN DATE 6/20/2026",
             start_date_time="2026-06-19T18:00:00.000"),
    ]
    src = NYCPermittedEventsSource()
    monkeypatch.setattr(src, "_fetch_rows", lambda: rows)
    events = list(src.fetch())
    permit_dates = {
        e.external_id.split(":", 1)[0]: sorted(set([
            ev.start_dt.day for ev in events if ev.external_id.startswith(
                e.external_id.split(":", 1)[0]
            )
        ]))
        for e in events
    }
    # permit-99: only May 16 kept (May 30 dropped as rain date occurrence)
    assert permit_dates["permit-99"] == [16]
    # permit-50: both June 1 and June 8 kept
    assert permit_dates["permit-50"] == [1, 8]
    # permit-77: June 19 (primary) kept; "6/20" rain date didn't match
    assert permit_dates["permit-77"] == [19]


def test_rain_date_without_specific_date_keeps_row(monkeypatch):
    # Title contains "Rain Date" with no explicit date after it. We can't
    # tell which is the rain-day occurrence, so keep the row.
    rows = [
        _row(event_id="permit-22",
             event_name="Autism Walk and Resource Fair Rain Date",
             start_date_time="2026-05-31T10:00:00.000"),
    ]
    src = NYCPermittedEventsSource()
    monkeypatch.setattr(src, "_fetch_rows", lambda: rows)
    assert len(list(src.fetch())) == 1


# --- end-to-end on real captured fixture ---------------------------------


def test_real_fixture_yields_at_least_one_event(monkeypatch):
    rows = json.loads(FIXTURE.read_text())
    assert len(rows) > 0, "fixture should not be empty"
    src = NYCPermittedEventsSource()
    monkeypatch.setattr(src, "_fetch_rows", lambda: rows)
    events = list(src.fetch())
    # Filtering is aggressive — we don't assert exact count, just that some
    # survive and they're shaped correctly.
    assert len(events) >= 1
    for ev in events:
        assert ev.source == "nyc_permitted_events"
        assert ev.start_dt.tzinfo is not None
        assert ev.price == Price.UNKNOWN
