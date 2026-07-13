"""Parser tests for the Brooklyn Botanic Garden source.

Uses the captured month-view fixture (tests/fixtures/bbg_sample.html — the
live July 2026 calendar page, 2026-07-13). No network calls.
"""

from __future__ import annotations

import pathlib
from zoneinfo import ZoneInfo

from nyc_events.models import Borough, Price
from nyc_events.sources.bbg import _parse_times, parse_month_page

NYC_TZ = ZoneInfo("America/New_York")

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "bbg_sample.html"


def _events():
    return parse_month_page(FIXTURE.read_text())


def test_only_family_tagged_cards_pass():
    events = _events()
    assert events, "no events parsed from fixture"
    titles = {e.title for e in events}
    # Family-tagged programs present…
    assert "Summer Family Discovery Weekends" in titles
    assert "Garden Adventures" in titles  # Children's Garden Classes tag
    # …adult/member/exhibit cards are not.
    assert "Ancestral Ecologies" not in titles  # Ongoing section (Exhibits)
    assert not any("Member" in t for t in titles)


def test_happy_path_occurrence():
    ev = next(e for e in _events() if e.title == "Summer Family Discovery Weekends")
    assert ev.source == "bbg"
    assert ev.venue_name == "Brooklyn Botanic Garden"
    assert ev.borough == Borough.BROOKLYN
    assert ev.price == Price.UNKNOWN
    local = ev.start_dt.astimezone(NYC_TZ)
    assert (local.hour, local.minute) == (10, 30)  # "10:30 a.m.–12:30 p.m."
    end_local = ev.end_dt.astimezone(NYC_TZ)
    assert (end_local.hour, end_local.minute) == (12, 30)
    assert ev.url and ev.url.startswith("https://www.bbg.org/")
    assert ev.description
    assert "Learn More" not in ev.description
    assert {"family", "best for kids", "nature"} <= set(ev.tags)


def test_recurring_program_gets_one_event_per_date_header():
    weekends = [e for e in _events() if e.title == "Summer Family Discovery Weekends"]
    assert len(weekends) > 1  # listed under each Sat/Sun it runs
    dates = {e.start_dt.date() for e in weekends}
    assert len(dates) == len(weekends)  # one per calendar day
    ids = {e.id for e in weekends}
    assert len(ids) == len(weekends)  # external_id carries the date


def test_garden_adventures_time_and_tags():
    ev = next(e for e in _events() if e.title == "Garden Adventures")
    local = ev.start_dt.astimezone(NYC_TZ)
    assert (local.hour, local.minute) == (9, 0)  # "9 a.m.–1 p.m."
    assert "educational" in ev.tags


def test_time_prose_parsing_units():
    assert _parse_times("10:30 a.m.–12:30 p.m.") == ((10, 30), (12, 30))
    assert _parse_times("9 a.m.–1 p.m.") == ((9, 0), (13, 0))
    assert _parse_times("12 p.m.–4 p.m.") == ((12, 0), (16, 0))
    assert _parse_times("Thursdays, July 16–August 13, 2026 | 10:30 a.m.–12:30 p.m.") == (
        (10, 30),
        (12, 30),
    )
    # Date-range numbers without a meridiem never parse as times.
    assert _parse_times("May 23–October 25, 2026") == ((0, 0), None)
    assert _parse_times("") == ((0, 0), None)
