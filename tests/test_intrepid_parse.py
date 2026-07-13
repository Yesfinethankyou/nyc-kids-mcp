"""Parser tests for the Intrepid Museum source.

Exercises the pure `parse_calendar` against a captured calendar-page fixture
(tests/fixtures/intrepid_calendar_page.html — page 1, captured 2026-07-13)
plus small inline HTML cards. No network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from nyc_events.models import Borough, Price
from nyc_events.sources.intrepid import (
    VENUE_NAME,
    _infer_tags,
    _is_kid_relevant,
    parse_calendar,
)

FIXTURE = Path(__file__).parent / "fixtures" / "intrepid_calendar_page.html"
TODAY = dt.date(2026, 7, 13)


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text()


@pytest.fixture(scope="module")
def events(html: str):
    return parse_calendar(html, TODAY, 60)


# ---------------------------------------------------------------------------
# Kid-relevance (inclusive + adult blocklist)
# ---------------------------------------------------------------------------


def test_after_hours_dropped():
    assert _is_kid_relevant("Intrepid After Hours: Photography Workshop", "") is False


def test_tasting_and_gala_dropped():
    assert _is_kid_relevant("Summer Tasting Fest", "craft beer and wine") is False
    assert _is_kid_relevant("Annual Gala", "black-tie fundraiser") is False


def test_family_program_kept():
    assert _is_kid_relevant("Movie Night - National Treasure", "family film") is True
    assert _is_kid_relevant("Early Morning Opening: Soaring Science", "for families") is True


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_tags_infer():
    assert "outer space" in _infer_tags("Astro Live", "astronomy show")
    assert "movies" in _infer_tags("Movie Night", "a film screening")
    assert "best for kids" in _infer_tags("Access Family Program", "for children")


# ---------------------------------------------------------------------------
# Fixture parse
# ---------------------------------------------------------------------------


def test_fixture_yields_events(events):
    assert len(events) >= 4
    assert all(e.borough == Borough.MANHATTAN for e in events)
    assert all(e.venue_name == VENUE_NAME for e in events)
    assert all(e.source == "intrepid" for e in events)


def test_after_hours_absent_from_fixture(events):
    assert all("after hours" not in e.title.lower() for e in events)


def test_datetimes_have_offset(events):
    e = events[0]
    assert e.start_dt.tzinfo is not None
    # Most fixture cards carry a start + end.
    assert any(ev.end_dt is not None for ev in events)


def test_external_id_keys_occurrence(events):
    e = events[0]
    assert e.external_id is not None
    assert ":" in e.external_id  # url:start_iso


def test_ids_unique(events):
    ids = [e.id for e in events]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# Inline cards
# ---------------------------------------------------------------------------


def _card(title: str, start="2026-07-20T11:00:00-04:00", end="2026-07-20T13:00:00-04:00",
          href="/some-event", body="A program.") -> str:
    return (
        '<div class="card product-card">'
        f'<a href="{href}">'
        f'<div class="card--header h6">{title}</div>'
        f'<div class="card--header"><time datetime="{start}">start</time>'
        f' - <time datetime="{end}">end</time></div>'
        f'<div class="card--body"><p>{body}</p></div>'
        "</a></div>"
    )


def test_inline_kid_kept():
    evs = parse_calendar(_card("Free World Cup Watch Party"), TODAY, 60)
    assert len(evs) == 1
    assert evs[0].price == Price.FREE  # "Free" in title
    assert evs[0].venue_name == VENUE_NAME


def test_inline_adult_dropped():
    assert parse_calendar(_card("Intrepid After Hours: Jazz"), TODAY, 60) == []


def test_inline_out_of_window_dropped():
    far = _card("Space Camp", start="2027-01-05T11:00:00-05:00", end="2027-01-05T13:00:00-05:00")
    assert parse_calendar(far, TODAY, 60) == []


def test_inline_recurring_occurrences_distinct():
    # Same URL, two dates → two distinct occurrences.
    page = _card("Free World Cup", start="2026-07-14T15:00:00-04:00", href="/wc") + _card(
        "Free World Cup", start="2026-07-15T15:00:00-04:00", href="/wc"
    )
    evs = parse_calendar(page, TODAY, 60)
    assert len(evs) == 2
    assert len({e.id for e in evs}) == 2
