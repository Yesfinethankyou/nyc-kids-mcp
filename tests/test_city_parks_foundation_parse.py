"""Parser tests for the City Parks Foundation source.

Uses the captured fixture (tests/fixtures/city_parks_foundation_sample.json)
and inline dicts. Does not make network calls — the parser takes a dict.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.city_parks_foundation import (
    _infer_tags,
    _is_kid_relevant,
    _parse_row,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "city_parks_foundation_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text())["events"]


def _row(**overrides) -> dict:
    """Minimal valid kid-relevant CPF (Tribe) row for inline tests."""
    base = {
        "id": 19967,
        "title": "PuppetMobile: Pinocchio",
        "description": "<p>A touring puppet show for the whole family.</p>",
        "excerpt": "",
        "categories": [{"name": "PuppetMobile"}],
        "utc_start_date": "2026-07-13 15:00:00",
        "utc_end_date": "2026-07-13 16:00:00",
        "cost": "Free",
        "is_virtual": False,
        "url": "https://cityparksfoundation.org/events/pinocchio/",
        "venue": {"venue": "Staten Island", "url": "https://cityparksfoundation.org/venue/staten-island/"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Kid-relevance filtering (category allowlist)
# ---------------------------------------------------------------------------


def test_puppetmobile_passes():
    assert _is_kid_relevant(_row()) is True


def test_summerstage_passes():
    assert _is_kid_relevant(_row(categories=[{"name": "SummerStage"}])) is True


def test_all_summerstage_kept_even_with_adult_sounding_blurb():
    # Maintainer call: every SummerStage show is kept. The shared ADULT
    # blocklist is deliberately NOT applied to CPF, so a "21+"-ish word in a
    # concert blurb must not drop the row.
    row = _row(
        categories=[{"name": "SummerStage"}],
        title="Late Night Beats: DJ Set",
        description="<p>An evening concert. 21+ bar available on site.</p>",
    )
    assert _is_kid_relevant(row) is True


def test_volunteer_category_excluded():
    row = _row(categories=[{"name": "Volunteer: It's My Park"}], title="It's My Park at Sunset")
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_grants_category_excluded():
    row = _row(categories=[{"name": "Grants and More"}], title="Grant Info Session")
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_virtual_event_excluded():
    row = _row(is_virtual=True, venue={"venue": "Online"})
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_unmappable_venue_excluded():
    # A venue string that isn't one of the five boroughs (and non-virtual)
    # yields no borough → dropped rather than shipped with borough=None.
    row = _row(venue={"venue": "Online"})
    assert _is_kid_relevant(row) is False


# ---------------------------------------------------------------------------
# Borough derivation from venue.venue
# ---------------------------------------------------------------------------


def test_borough_from_venue_string():
    assert _parse_row(_row(venue={"venue": "Manhattan"})).borough == Borough.MANHATTAN
    assert _parse_row(_row(venue={"venue": "Brooklyn"})).borough == Borough.BROOKLYN
    assert _parse_row(_row(venue={"venue": "Queens"})).borough == Borough.QUEENS
    assert _parse_row(_row(venue={"venue": "Bronx"})).borough == Borough.BRONX
    assert _parse_row(_row(venue={"venue": "Staten Island"})).borough == Borough.STATEN_ISLAND


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_puppetmobile_tags():
    tags = _infer_tags(_row(categories=[{"name": "PuppetMobile"}]))
    assert "family" in tags
    assert "best for kids" in tags
    assert "puppet" in tags


def test_summerstage_tags():
    tags = _infer_tags(_row(categories=[{"name": "SummerStage"}]))
    assert "family" in tags
    assert "music" in tags
    assert "concert" in tags


# ---------------------------------------------------------------------------
# Happy-path row parse (fixture)
# ---------------------------------------------------------------------------


def test_happy_path_puppetmobile_from_fixture():
    row = next(e for e in _load_events() if e["id"] == 19967)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.source == "city_parks_foundation"
    assert ev.external_id == "19967"
    assert ev.start_dt == datetime(2026, 7, 13, 15, 0, 0, tzinfo=UTC)
    assert ev.borough == Borough.STATEN_ISLAND
    assert ev.venue_name is None
    assert ev.price == Price.FREE
    assert "best for kids" in ev.tags


def test_happy_path_summerstage_from_fixture():
    row = next(e for e in _load_events() if e["id"] == 19774)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.borough == Borough.QUEENS
    assert ev.price == Price.FREE
    assert "music" in ev.tags


def test_entity_decoded_title_from_fixture():
    # Bronx SummerStage row uses &#038; in the title.
    row = next(e for e in _load_events() if e["id"] == 19780)
    ev = _parse_row(row)
    assert ev is not None
    assert "&#038;" not in ev.title
    assert "&" in ev.title


def test_fixture_excluded_rows_dropped():
    for eid in (20953, 20697, 20770):
        row = next(e for e in _load_events() if e["id"] == eid)
        assert _parse_row(row) is None


def test_per_occurrence_ids_distinct():
    # The PuppetMobile tour repeats the same title on different dates with a
    # distinct id per occurrence — external_id must not collapse them.
    puppet = [e for e in _load_events() if "PuppetMobile" in [c["name"] for c in e["categories"]]]
    ids = {e["id"] for e in puppet}
    assert len(ids) >= 3
