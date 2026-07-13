"""Parser tests for the NYPL source.

Exercises the pure `parse_rows` against a captured listing fixture
(tests/fixtures/nypl_calendar_kids_page.html — one Manhattan kids page,
captured 2026-07-13) plus small inline HTML rows. No network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from nyc_events.models import Borough, Price
from nyc_events.sources.nypl import (
    _infer_tags,
    _parse_age_range,
    _parse_occurrence_dt,
    parse_rows,
)

FIXTURE = Path(__file__).parent / "fixtures" / "nypl_calendar_kids_page.html"
TODAY = dt.date(2026, 7, 13)  # the fixture's capture date


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text()


@pytest.fixture(scope="module")
def events(html: str):
    return parse_rows(html, Borough.MANHATTAN, TODAY, 60)


# ---------------------------------------------------------------------------
# Time-cell parsing
# ---------------------------------------------------------------------------


def test_parse_today_pm():
    d = _parse_occurrence_dt("Today @ 2 PM", TODAY, TODAY + dt.timedelta(days=60))
    assert d is not None
    assert (d.year, d.month, d.day, d.hour, d.minute) == (2026, 7, 13, 14, 0)


def test_parse_today_with_minutes():
    d = _parse_occurrence_dt("Today @ 10:30 AM", TODAY, TODAY + dt.timedelta(days=60))
    assert (d.hour, d.minute) == (10, 30)


def test_parse_absolute_weekday_resolves_year_into_window():
    d = _parse_occurrence_dt("Tue, July 14 @ 2 PM", TODAY, TODAY + dt.timedelta(days=60))
    assert d is not None
    assert (d.year, d.month, d.day, d.hour) == (2026, 7, 14, 14)


def test_parse_absolute_next_year_when_month_already_passed():
    # A January date seen from a mid-July "today" resolves to next year.
    today = dt.date(2026, 7, 13)
    d = _parse_occurrence_dt("Fri, January 8 @ 11 AM", today, today + dt.timedelta(days=200))
    assert d is not None
    assert (d.year, d.month, d.day) == (2027, 1, 8)


def test_parse_unparseable_returns_none():
    assert _parse_occurrence_dt("Ongoing", TODAY, TODAY + dt.timedelta(days=60)) is None


# ---------------------------------------------------------------------------
# Age + tag helpers
# ---------------------------------------------------------------------------


def test_age_range_parsed():
    assert _parse_age_range("A craft for children ages 6-12.") == (6, 12)
    assert _parse_age_range("For ages 3 to 5.") == (3, 5)


def test_age_range_rejects_nonsense():
    assert _parse_age_range("no ages here") == (None, None)
    assert _parse_age_range("open 9-5") == (None, None)  # no "ages" keyword
    assert _parse_age_range("ages 12-6") == (None, None)  # hi < lo → rejected
    assert _parse_age_range("ages 40-60") == (None, None)  # > 18 → not a kids range


def test_best_for_kids_only_for_child_audiences():
    assert "best for kids" in _infer_tags("Storytime", "songs", "children, families")
    # Teens-only does not earn best-for-kids.
    teen_tags = _infer_tags("Teen Lab", "coding", "teens/young adults (13-18 years)")
    assert "best for kids" not in teen_tags


def test_tags_infer_from_text():
    tags = _infer_tags("Baby Lapsit Storytime", "songs and rhymes", "children, infant")
    assert "storytelling" in tags
    assert "family" in tags


# ---------------------------------------------------------------------------
# Fixture parse
# ---------------------------------------------------------------------------


def test_fixture_yields_kid_events(events):
    assert len(events) >= 15


def test_all_borough_and_free(events):
    assert all(e.borough == Borough.MANHATTAN for e in events)
    assert all(e.price == Price.FREE for e in events)
    assert all(e.source == "nypl" for e in events)


def test_no_virtual_online_rows(events):
    assert all(not (e.venue_name or "").lower().startswith("online") for e in events)


def test_venue_is_branch_name(events):
    # Branches feed the library neighborhood table → must be the branch name.
    assert any((e.venue_name or "").endswith("Library") for e in events)


def test_external_id_is_url_plus_occurrence(events):
    e = next(e for e in events if e.venue_name == "Chatham Square Library"
             and "Summer Learners Lab" in e.title)
    assert e.external_id is not None
    assert e.external_id.endswith(":2026-07-13T13:00")
    assert "/events/programs/" in e.external_id


def test_ids_are_unique(events):
    ids = [e.id for e in events]
    assert len(set(ids)) == len(ids)


def test_adult_only_rows_dropped(events, html):
    # The fixture page includes Adults-only rows (the server audience filter is
    # loose); the kept set must be strictly smaller than the total row count,
    # proving the client-side kid gate actually drops rows.
    total_rows = html.count('class="col-4"')
    assert 0 < len(events) < total_rows


# ---------------------------------------------------------------------------
# Inline HTML: client-side gates
# ---------------------------------------------------------------------------


def _page(rows_html: str) -> str:
    return f"<table><tbody>{rows_html}</tbody></table>"


def _row(*, time="Today @ 11 AM", title="Storytime", href="/events/programs/2026/07/13/x",
         desc="Songs and rhymes.", loc="Test Library", audience="Children") -> str:
    return (
        f'<tr class="col-4">'
        f'<td class="views-field event-time">{time}</td>'
        f'<td class="views-field event-title views-field-title">'
        f'<div class="event-name"><a href="{href}">{title}</a></div>'
        f'<div class="description">{desc}</div></td>'
        f'<td class="views-field event-location">{loc}</td>'
        f'<td class="views-field event-audience">{audience}</td>'
        f"</tr>"
    )


def test_inline_adult_row_dropped():
    evs = parse_rows(_page(_row(audience="Adults, 50+")), Borough.BRONX, TODAY, 60)
    assert evs == []


def test_inline_online_row_dropped():
    evs = parse_rows(_page(_row(loc="Online")), Borough.BRONX, TODAY, 60)
    assert evs == []


def test_inline_out_of_window_dropped():
    evs = parse_rows(_page(_row(time="Sat, December 25 @ 11 AM")), Borough.BRONX, TODAY, 60)
    assert evs == []


def test_inline_kid_row_kept_with_borough():
    evs = parse_rows(_page(_row(audience="Children, Families")), Borough.STATEN_ISLAND, TODAY, 60)
    assert len(evs) == 1
    assert evs[0].borough == Borough.STATEN_ISLAND
    assert evs[0].venue_name == "Test Library"
