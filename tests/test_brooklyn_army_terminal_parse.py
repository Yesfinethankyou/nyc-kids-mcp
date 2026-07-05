"""Parser tests for the Brooklyn Army Terminal source.

Exercises the parser directly against the captured fixture (no httpx mock).
Fixture captured live 2026-06-15 from https://brooklynarmyterminal.com/events
(24 event cards; 12 "Live Music Concert" 21+ EDM shows, 12 community events).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nyc_events.models import Borough, Price, compute_id
from nyc_events.sources.brooklyn_army_terminal import (
    VENUE_NAME,
    _parse_start_time,
    parse_events,
)

FIXTURE = Path(__file__).parent / "fixtures" / "brooklyn_army_terminal_sample.html"


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text()


@pytest.fixture(scope="module")
def events(html: str):
    return parse_events(html)


@pytest.fixture(scope="module")
def by_title(events):
    return {e.title: e for e in events}


def test_fixture_has_24_cards(html: str):
    assert html.count("events-full-width__grid-card") == 24


def test_live_music_concerts_dropped(by_title):
    assert all(not t.lower().startswith("live music concert") for t in by_title)


def test_kept_community_event_count(events):
    # 24 cards − 12 "Live Music Concert" 21+ shows = 12 kept community events.
    assert len(events) == 12


def test_happy_path_row(by_title):
    ev = by_title["Summer at the Terminal: Latin Flavors and Culture"]
    assert ev.source == "brooklyn_army_terminal"
    assert ev.venue_name == VENUE_NAME
    assert ev.borough is Borough.BROOKLYN
    # "1:00-7:00pm" → start 13:00 (pm borrowed from end of range).
    assert ev.start_dt.year == 2026
    assert ev.start_dt.month == 7
    assert ev.start_dt.day == 12
    assert ev.start_dt.hour == 13
    assert ev.start_dt.minute == 0
    assert ev.price is Price.FREE
    assert ev.description  # subtitle populated
    assert "family" in ev.tags
    assert "cultural" in ev.tags


def test_am_time_parsed(by_title):
    # "10:00am-2:00pm" → 10:00.
    ev = by_title["Summer at the Terminal: Wellness on the Waterfront"]
    assert ev.start_dt.hour == 10
    assert ev.start_dt.minute == 0


def test_external_url_kept_when_present(by_title):
    ev = by_title["Rooftop Films Screening"]
    assert ev.url == "https://rooftopfilms.com/calendar/"


def test_url_none_when_absent(by_title):
    # Plain "Summer at the Terminal" markets have no card link.
    ev = by_title["Summer at the Terminal: Salsa Night"]
    assert ev.url is None


def test_all_kept_events_free(events):
    # No kept community event links to a ticketing host on the captured page.
    assert all(e.price is Price.FREE for e in events)


def test_no_external_id(events):
    assert all(e.external_id is None for e in events)


def test_low_confidence_inputs_populated(events):
    # description from subtitle is always present, so events aren't low-confidence
    # purely on the description==None && url==None rule.
    assert all(e.description for e in events)


def test_compute_id_is_title_venue_date(by_title):
    ev = by_title["Día de Los Muertos Celebration"]
    expected = compute_id(
        "brooklyn_army_terminal",
        title=ev.title,
        venue=VENUE_NAME,
        date_iso=ev.start_dt.date().isoformat(),
    )
    assert ev.id == expected


def test_ids_unique(events):
    assert len({e.id for e in events}) == len(events)


def test_start_dt_is_tz_aware(events):
    # db._iso rejects naive datetimes, so every Event must carry tzinfo.
    assert events  # guard against an empty parse silently passing
    assert all(e.start_dt.tzinfo is not None for e in events)


def test_events_survive_db_upsert(events, tmp_path):
    # Regression: parser tests alone never crossed the DB boundary, so a
    # naive start_dt (rejected by db._iso) crashed the whole ingest unseen.
    from nyc_events import db

    db_path = str(tmp_path / "events.db")
    db.init_events(db_path)
    with db.connect_events(db_path) as conn:
        ins, _ = db.upsert_events(conn, events)
        assert ins == len(events)
        stored = db.search(conn, limit=100)
    assert len(stored) == len(events)


@pytest.mark.parametrize(
    ("time_str", "expected"),
    [
        ("1:00-7:00pm", (13, 0)),
        ("10:00am-2:00pm", (10, 0)),
        ("3:00-10:00pm", (15, 0)),
        ("4:00-8:00pm", (16, 0)),
        ("12:00-3:00pm", (12, 0)),
        ("", (0, 0)),
        ("all day", (0, 0)),
    ],
)
def test_parse_start_time(time_str, expected):
    assert _parse_start_time(time_str) == expected
