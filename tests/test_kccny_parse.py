"""Parser tests for the Korean Cultural Center New York (KCCNY) source.

Uses the captured /education-literature list fixture
(tests/fixtures/kccny_sample.html — real cards from a live fetch,
2026-07-23). No network calls.
"""

from __future__ import annotations

import pathlib
from zoneinfo import ZoneInfo

from nyc_events.models import Borough, Price
from nyc_events.sources.kccny import (
    _is_kid_relevant,
    _parse_time_range,
    parse_collection_page,
)

NYC_TZ = ZoneInfo("America/New_York")

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "kccny_sample.html"


def _events():
    return parse_collection_page(FIXTURE.read_text())


def test_multi_session_row_expands_into_two_events():
    events = [e for e in _events() if e.title == "A K-Birthday Party"]
    assert len(events) == 2
    dates = sorted(e.start_dt.astimezone(NYC_TZ).date().isoformat() for e in events)
    assert dates == ["2026-08-07", "2026-08-08"]
    ids = {e.id for e in events}
    assert len(ids) == 2  # external_id carries the occurrence date


def test_happy_path_multi_session_fields():
    ev = next(
        e
        for e in _events()
        if e.title == "A K-Birthday Party"
        and e.start_dt.astimezone(NYC_TZ).date().isoformat() == "2026-08-07"
    )
    assert ev.source == "kccny"
    assert ev.venue_name == "Korean Cultural Center New York"
    assert ev.borough == Borough.MANHATTAN
    local_start = ev.start_dt.astimezone(NYC_TZ)
    local_end = ev.end_dt.astimezone(NYC_TZ)
    assert (local_start.hour, local_start.minute) == (16, 0)  # 4:00 PM
    assert (local_end.hour, local_end.minute) == (17, 30)  # 5:30 PM
    assert ev.age_min == 6
    assert ev.age_max == 9
    assert ev.url and ev.url.endswith("/education-literature/a-k-birthday-party")
    assert "family" in ev.tags
    assert "best for kids" in ev.tags


def test_single_session_row_with_free_price():
    ev = next(e for e in _events() if e.title == "Korean Storytime: Gimbap Story")
    assert ev.start_dt.astimezone(NYC_TZ).date().isoformat() == "2025-10-22"
    local = ev.start_dt.astimezone(NYC_TZ)
    assert (local.hour, local.minute) == (16, 0)
    end_local = ev.end_dt.astimezone(NYC_TZ)
    assert (end_local.hour, end_local.minute) == (17, 0)
    assert ev.price == Price.FREE
    assert ev.age_min == 4
    assert ev.age_max == 6
    assert "story time" in ev.tags


def test_row_missing_optional_fields_empty_excerpt_is_skipped():
    # "Seollal Family Day" is title-kid-relevant but has no excerpt div at
    # all on the list page, so no date is parseable — this source does not
    # crawl detail pages (see module docstring), so it's dropped, not
    # fetched-and-recovered.
    titles = {e.title for e in _events()}
    assert "Seollal Family Day" not in titles


def test_adult_rows_are_filtered_out():
    titles = {e.title for e in _events()}
    assert not any("Korean language course" in t for t in titles)
    assert "Serang Chung in Conversation: First New York Book Talk" not in titles
    assert "The Other Korea: Stories from the Diaspora" not in titles


def test_is_kid_relevant_keyword_and_age_gate():
    assert _is_kid_relevant("Korean Storytime: Something", "")
    assert _is_kid_relevant("A Workshop", "Designed for ages 4-6")
    assert not _is_kid_relevant("Adult Language Course", "Tuition: $150 for 15 weeks")
    assert not _is_kid_relevant("Family Night", "This event is 21+ adults only")


def test_parse_time_range_implicit_shared_meridiem():
    assert _parse_time_range("4:00–5:30 PM") == ((16, 0), (17, 30))
    assert _parse_time_range("3:00 PM") == ((15, 0), None)
    assert _parse_time_range("no time here") == ((0, 0), None)
