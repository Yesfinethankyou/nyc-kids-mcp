"""Parser tests for the QPL source.

Exercises the pure `parse_calendar` / helpers against a captured listing
fixture (tests/fixtures/qpl_calendar_page.html — one calendar page captured
2026-07-13) plus inline JSON. No network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from nyc_events.models import Borough, Price
from nyc_events.sources.qpl import (
    _extract_cards,
    _infer_tags,
    _is_kid_age,
    _parse_age_range,
    parse_calendar,
)

FIXTURE = Path(__file__).parent / "fixtures" / "qpl_calendar_page.html"
TODAY = dt.date(2026, 7, 13)  # the fixture's capture date


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text()


@pytest.fixture(scope="module")
def events(html: str):
    return parse_calendar(html, TODAY, 60)


# ---------------------------------------------------------------------------
# Age / kid-gate helpers
# ---------------------------------------------------------------------------


def test_kid_gate():
    assert _is_kid_age("Kids(0-5), Kids(6-11)") is True
    assert _is_kid_age("Adults, Kids(0-5)") is True  # family program kept
    assert _is_kid_age("Teens(12-18)") is True
    assert _is_kid_age("Adults, Seniors") is False
    assert _is_kid_age("Adults") is False


def test_age_range_spans_all_bands():
    assert _parse_age_range("Kids(0-5), Kids(6-11)") == (0, 11)
    assert _parse_age_range("Kids(6-11)") == (6, 11)
    assert _parse_age_range("Adults, Seniors") == (None, None)


def test_teens_only_not_best_for_kids():
    from nyc_events.sources.qpl import _CardData

    card = _CardData(
        job_id="x", title="Teen Coding", description=None, url=None,
        prgm_age="Teens(12-18)", prgm_type="Workshop", branch="Flushing",
        delivery_format="In-Person", start_ts=0,
    )
    tags = _infer_tags(card)
    assert "family" in tags
    assert "best for kids" not in tags


# ---------------------------------------------------------------------------
# Card extraction + fixture parse
# ---------------------------------------------------------------------------


def test_extracts_all_cards(html: str):
    # 12 cards on the page (all audiences).
    assert len(_extract_cards(html)) == 12


def test_fixture_yields_kid_events(events):
    assert len(events) >= 6
    assert all(e.borough == Borough.QUEENS for e in events)
    assert all(e.price == Price.FREE for e in events)
    assert all(e.source == "qpl" for e in events)


def test_adult_cards_dropped(events):
    # 12 cards, some Adults-only → fewer kept.
    assert len(events) < 12


def test_external_id_is_job_id(events):
    e = next(e for e in events if e.venue_name == "South Hollis Library")
    assert e.external_id == "019359-0626"
    assert e.url == "https://www.queenslibrary.org/calendar/summer-at-the-library-lunch-and-a-show/019359-0626"


def test_authoritative_timestamp(events):
    # date_show_timestamp drives the datetime (no year-guessing).
    e = next(e for e in events if e.venue_name == "South Hollis Library")
    assert e.start_dt.year == 2026
    assert e.start_dt.tzinfo is not None


def test_age_and_venue(events):
    e = next(e for e in events if e.venue_name == "Cambria Heights Library")
    assert (e.age_min, e.age_max) == (0, 5)
    assert e.venue_name == "Cambria Heights Library"  # branch → library table


def test_ids_unique(events):
    ids = [e.id for e in events]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# Inline JSON: gates
# ---------------------------------------------------------------------------


def _page(*blobs: str) -> str:
    scripts = "".join(
        f"<script>arrJsonData_cal['x{i}'] = '{b}';</script>"
        for i, b in enumerate(blobs)
    )
    return f"<html><body>{scripts}</body></html>"


def _blob(**over) -> str:
    import json

    base = {
        "jobID": "099999-0626",
        "title": "Storytime",
        "descrQV": "Songs and rhymes...",
        "callUrl": "/calendar/storytime/099999-0626",
        "prgm_age": "Kids(0-5)",
        "prgm_type": "Storytime",
        "branch_name": "Flushing",
        "delivery_format": "In-Person",
        "date_show_timestamp": 1783962000,  # Jul 13 2026 in ET
    }
    base.update(over)
    return json.dumps(base).replace("'", "&#039;")


def test_inline_adult_dropped():
    assert parse_calendar(_page(_blob(prgm_age="Adults, Seniors")), TODAY, 60) == []


def test_inline_online_dropped():
    assert parse_calendar(_page(_blob(delivery_format="Online")), TODAY, 60) == []


def test_inline_no_branch_dropped():
    assert parse_calendar(_page(_blob(branch_name="")), TODAY, 60) == []


def test_inline_out_of_window_dropped():
    # A timestamp far in the future (year 2028).
    assert parse_calendar(_page(_blob(date_show_timestamp=1830000000)), TODAY, 60) == []


def test_inline_kid_kept():
    evs = parse_calendar(_page(_blob()), TODAY, 60)
    assert len(evs) == 1
    assert evs[0].borough == Borough.QUEENS
    assert evs[0].venue_name == "Flushing Library"
    assert (evs[0].age_min, evs[0].age_max) == (0, 5)
