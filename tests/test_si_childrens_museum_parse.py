"""Parser tests for the Staten Island Children's Museum source.

Uses the captured fixture (tests/fixtures/si_childrens_museum_sample.json —
real Tribe REST rows, 2026-07-13) plus inline dicts. No network calls.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.si_childrens_museum import (
    _is_kid_relevant,
    _parse_row,
    _resolve_price,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "si_childrens_museum_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text())["events"]


def _first(title_prefix: str) -> dict:
    for row in _load_events():
        if row["title"].startswith(title_prefix):
            return row
    raise AssertionError(f"no fixture row titled {title_prefix!r}")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_row():
    ev = _parse_row(_first("Walk-In! Workshop: Postage Stamps"))
    assert ev is not None
    assert ev.source == "si_childrens_museum"
    assert ev.title == "Walk-In! Workshop: Postage Stamps"
    assert ev.venue_name == "Staten Island Children's Museum"
    assert ev.borough == Borough.STATEN_ISLAND
    assert ev.start_dt == datetime(2026, 7, 12, 16, 0, tzinfo=UTC)  # noon EDT
    assert ev.end_dt == datetime(2026, 7, 12, 20, 0, tzinfo=UTC)
    assert ev.url and "sichildrensmuseum.org/event/" in ev.url
    assert ev.description  # HTML stripped to prose
    assert "<" not in (ev.description or "")


def test_every_fixture_row_parses():
    rows = _load_events()
    events = [_parse_row(r) for r in rows]
    assert all(e is not None for e in events)


def test_recurring_ids_are_per_occurrence():
    # The two Postage Stamps rows are the same recurring program on different
    # dates — upstream gives each occurrence its own id, so external_id needs
    # no :date suffix (verified live 2026-07-13; see module docstring).
    rows = [r for r in _load_events() if r["title"].startswith("Walk-In! Workshop")]
    assert len(rows) == 2
    events = [_parse_row(r) for r in rows]
    assert events[0].external_id != events[1].external_id
    assert events[0].id != events[1].id
    assert events[0].start_dt != events[1].start_dt


# ---------------------------------------------------------------------------
# Price: empty cost + "Free" category
# ---------------------------------------------------------------------------


def test_free_category_maps_to_free_price():
    ev = _parse_row(_first("Pre-K Resource Fair"))
    assert ev is not None
    assert ev.price == Price.FREE


def test_empty_cost_without_free_category_is_unknown():
    ev = _parse_row(_first("Boogie Woogie Wednesday: Zumba Kids"))
    assert ev is not None
    assert ev.price == Price.UNKNOWN


def test_explicit_cost_string_wins_over_category():
    assert _resolve_price({"cost": "$8"}, {"Free"}) == Price.PAID


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_category_driven_tags():
    ev = _parse_row(_first("Crazy Science Experiments"))
    assert ev is not None
    assert "family" in ev.tags
    assert "best for kids" in ev.tags  # Event for Kids / Family Friendly
    assert "science" in ev.tags  # STEM

    ev = _parse_row(_first("Walk-In! Workshop"))
    assert "arts & crafts" in ev.tags  # art / Art-Making / crafts


# ---------------------------------------------------------------------------
# Filter net (defensive only — no live rows trigger it)
# ---------------------------------------------------------------------------


def test_adult_title_net_drops_defensively():
    row = dict(_first("Bee Day") if any(
        r["title"] == "Bee Day" for r in _load_events()
    ) else _load_events()[0])
    row["title"] = "Museum After Dark: Adults Only Night"
    assert not _is_kid_relevant(row)
    assert _parse_row(row) is None


def test_row_without_title_is_skipped():
    row = dict(_load_events()[0])
    row["title"] = ""
    assert _parse_row(row) is None


def test_row_without_start_date_is_skipped():
    row = dict(_load_events()[0])
    row["utc_start_date"] = None
    assert _parse_row(row) is None
