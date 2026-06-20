"""Parser tests for the Domino Park source.

Uses the captured fixture (tests/fixtures/domino_park_sample.json, a real slice
of the public Sanity feed) and inline dicts. No network calls — the parser
takes a dict. Tests pin `today` for deterministic recurrence expansion.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.domino_park import (
    NYC_TZ,
    _infer_tags,
    _is_kid_relevant,
    _occurrence_dates,
    _parse_event,
    _parse_hour,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "domino_park_sample.json"

# Pinned "today" so fixture events (mid-2026) fall in a deterministic window.
TODAY = date(2026, 6, 10)


def _load_docs() -> list[dict]:
    return json.loads(FIXTURE.read_text())["result"]


def _doc(title: str) -> dict:
    return next(d for d in _load_docs() if d["title"].strip() == title)


def _events(title: str, today: date = TODAY) -> list:
    return _parse_event(_doc(title), today)


def _inline(**overrides) -> dict:
    base = {
        "_id": "abc-123",
        "title": "Family Craft Workshop",
        "slug": {"current": "family-craft-workshop"},
        "description": "A hands-on craft for kids and families.",
        "startDate": "2026-06-20",
        "endDate": None,
        "startHour": "11:00 am",
        "endHour": "1:00 pm",
        "variant": "single-day",
        "frequency": None,
        "interval": None,
        "tags": ["Family & Education"],
        "location": "Domino Square",
        "latitude": 40.7149,
        "longitude": -73.9678,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Hour parsing
# ---------------------------------------------------------------------------


def test_parse_hour_formats():
    assert _parse_hour("6 pm") == (18, 0)
    assert _parse_hour("10:00 AM") == (10, 0)
    assert _parse_hour("7:30 pm ") == (19, 30)
    assert _parse_hour("8:00am") == (8, 0)
    assert _parse_hour("12 pm") == (12, 0)  # noon
    assert _parse_hour("12 am") == (0, 0)  # midnight
    assert _parse_hour("4pm") == (16, 0)


def test_parse_hour_unparseable_returns_none():
    assert _parse_hour(None) is None
    assert _parse_hour("") is None
    assert _parse_hour("all day") is None


# ---------------------------------------------------------------------------
# Recurrence expansion (unit)
# ---------------------------------------------------------------------------


def test_weekly_interval_1():
    dates = _occurrence_dates(
        date(2026, 6, 20), date(2026, 7, 11), "weekly", 1,
        date(2026, 6, 1), date(2026, 12, 1),
    )
    assert dates == [date(2026, 6, 20), date(2026, 6, 27),
                     date(2026, 7, 4), date(2026, 7, 11)]


def test_weekly_interval_2_is_biweekly():
    dates = _occurrence_dates(
        date(2026, 6, 29), date(2026, 8, 1), "weekly", 2,
        date(2026, 6, 1), date(2026, 12, 1),
    )
    assert dates == [date(2026, 6, 29), date(2026, 7, 13), date(2026, 7, 27)]


def test_monthly_clamps_to_window_and_enddate():
    dates = _occurrence_dates(
        date(2026, 7, 2), date(2026, 9, 3), "monthly", 1,
        date(2026, 8, 1), date(2026, 12, 1),
    )
    # July 2 is before win_start (Aug 1), so only Aug 2 + Sep 2.
    assert dates == [date(2026, 8, 2), date(2026, 9, 2)]


def test_daily_expansion():
    dates = _occurrence_dates(
        date(2026, 6, 20), date(2026, 6, 23), "daily", 1,
        date(2026, 6, 1), date(2026, 12, 1),
    )
    assert dates == [date(2026, 6, 20), date(2026, 6, 21),
                     date(2026, 6, 22), date(2026, 6, 23)]


def test_occurrences_bounded_by_window_end():
    dates = _occurrence_dates(
        date(2026, 6, 20), None, "weekly", 1,
        date(2026, 6, 20), date(2026, 7, 4),
    )
    assert dates == [date(2026, 6, 20), date(2026, 6, 27), date(2026, 7, 4)]


# ---------------------------------------------------------------------------
# variant handling (the crux: variant is authoritative, not frequency)
# ---------------------------------------------------------------------------


def test_single_day_with_vestigial_frequency_yields_one_event():
    # "Longevity Stick" is variant=single-day but carries weekly/interval-2 and
    # a multi-month endDate. That recurrence data is vestigial — it must NOT
    # expand; exactly one event on its startDate.
    evs = _events("Longevity Stick")
    assert len(evs) == 1
    assert evs[0].external_id == _doc("Longevity Stick")["_id"]
    assert evs[0].start_dt.date() == date(2026, 6, 21)


def test_reoccurring_expands_into_multiple_occurrences():
    # "Craft Nights": variant=reoccurring, weekly interval 2, 2026-06-29..09-28.
    evs = _events("Craft Nights")
    assert len(evs) > 1
    # Biweekly spacing, all on the same weekday as the start (Monday).
    days = sorted(e.start_dt.date() for e in evs)
    assert days[0] == date(2026, 6, 29)
    for a, b in zip(days, days[1:], strict=False):
        assert (b - a).days == 14
    # Per-occurrence external_ids.
    doc_id = _doc("Craft Nights")["_id"]
    assert all(e.external_id == f"{doc_id}:{e.start_dt.date().isoformat()}" for e in evs)
    # Within the 60-day window from TODAY.
    assert all(TODAY <= e.start_dt.date() <= date(2026, 8, 9) for e in evs)


def test_reoccurring_monthly():
    # "Dominoes Socials": monthly, 2026-07-02..09-03. Window TODAY..08-09.
    evs = _events("Dominoes Socials presented by Capicu!")
    days = sorted(e.start_dt.date() for e in evs)
    assert days == [date(2026, 7, 2), date(2026, 8, 2)]


def test_multi_day_is_single_event_spanning_range():
    # Use an early TODAY so the June 17-18 multi-day is in-window.
    evs = _parse_event(_doc("HOLE PICS"), date(2026, 6, 10))
    assert len(evs) == 1
    ev = evs[0]
    assert ev.start_dt.date() == date(2026, 6, 17)
    assert ev.end_dt is not None
    assert ev.end_dt.date() == date(2026, 6, 18)
    assert ev.external_id == _doc("HOLE PICS")["_id"]


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------


def test_past_single_day_is_dropped():
    # "Winter Workshops" single-day on 2026-02-18 — well before TODAY.
    assert _events("Winter Workshops") == []


def test_past_multi_day_is_dropped():
    # HOLE PICS spans June 17-18; with TODAY=2026-07-01 it's fully past.
    assert _parse_event(_doc("HOLE PICS"), date(2026, 7, 1)) == []


def test_single_day_outside_future_window_dropped():
    far = _inline(startDate="2027-01-01")
    assert _parse_event(far, TODAY) == []


# ---------------------------------------------------------------------------
# Happy-path field mapping (fixture + inline)
# ---------------------------------------------------------------------------


def test_happy_path_single_day_fields():
    evs = _events("Juneberry Festival")
    assert len(evs) == 1
    ev = evs[0]
    assert ev.source == "domino_park"
    assert ev.title == "Juneberry Festival"
    assert ev.venue_name == "Domino Park"
    assert ev.borough == Borough.BROOKLYN
    assert ev.price == Price.UNKNOWN
    assert ev.start_dt.tzinfo is not None
    assert ev.start_dt == datetime(2026, 6, 20, 13, 0, tzinfo=NYC_TZ)  # "1 pm"
    assert ev.url.startswith("https://www.dominopark.com/events/")
    assert ev.lat is not None and ev.lng is not None
    assert "family" in ev.tags


def test_inline_combines_date_and_hour_local():
    ev = _parse_event(_inline(), TODAY)[0]
    assert ev.start_dt == datetime(2026, 6, 20, 11, 0, tzinfo=NYC_TZ)
    assert ev.end_dt == datetime(2026, 6, 20, 13, 0, tzinfo=NYC_TZ)


def test_unparseable_hour_falls_back_to_midnight():
    ev = _parse_event(_inline(startHour="TBD", endHour=None), TODAY)[0]
    assert ev.start_dt == datetime(2026, 6, 20, 0, 0, tzinfo=NYC_TZ)
    assert ev.end_dt is None


def test_raw_payload_is_full_doc():
    ev = _events("Juneberry Festival")[0]
    parsed = json.loads(ev.raw_payload)
    assert parsed["_id"] == _doc("Juneberry Festival")["_id"]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_adult_blocklist_drops_event():
    doc = _inline(title="Evening Mixer", description="Adults only, 21+ cocktails.")
    assert _is_kid_relevant(doc) is False
    assert _parse_event(doc, TODAY) == []


def test_bare_drag_not_blocklisted():
    # A family throwback/skate night themed around drag-racing or "drag" as a
    # verb must not be dropped; only "drag show"/"drag brunch" are excluded.
    doc = _inline(title="Decades Night", description="Dress up and drag the dance floor.")
    assert _is_kid_relevant(doc) is True


def test_missing_title_returns_empty():
    assert _parse_event(_inline(title=""), TODAY) == []


def test_missing_start_date_returns_empty():
    assert _parse_event(_inline(startDate=None), TODAY) == []


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------


def test_category_maps_family_to_best_for_kids():
    tags = _infer_tags("Play Session", None, ["Family & Education"])
    assert "best for kids" in tags
    assert "family" in tags


def test_keyword_tags_market_and_music():
    tags = _infer_tags("Greenmarket DJ Set", "weekly market with a dj", None)
    assert "market" in tags
    assert "music" in tags


# ---------------------------------------------------------------------------
# Fixture-wide invariants
# ---------------------------------------------------------------------------


def test_all_fixture_events_are_brooklyn_domino_unknown_price():
    for doc in _load_docs():
        for ev in _parse_event(doc, TODAY):
            assert ev.venue_name == "Domino Park"
            assert ev.borough == Borough.BROOKLYN
            assert ev.price == Price.UNKNOWN
            assert ev.start_dt.tzinfo is not None


def test_fixture_event_ids_unique():
    ids = [ev.id for doc in _load_docs() for ev in _parse_event(doc, TODAY)]
    assert ids
    assert len(ids) == len(set(ids)), "Duplicate event IDs across expansion"
