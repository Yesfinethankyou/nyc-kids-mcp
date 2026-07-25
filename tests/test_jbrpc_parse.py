"""Parser tests for Jamaica Bay-Rockaway Parks Conservancy (JBRPC).

Uses the captured /events list fixture (tests/fixtures/jbrpc_sample.html —
15 real cards, 9 upcoming + 6 past, captured 2026-07-25). No network calls.
"""

from __future__ import annotations

import pathlib
from datetime import UTC
from zoneinfo import ZoneInfo

from nyc_events.models import Borough, Price
from nyc_events.sources.jbrpc import (
    _infer_borough,
    _is_kid_relevant,
    parse_events_page,
)

NYC_TZ = ZoneInfo("America/New_York")

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "jbrpc_sample.html"


def _events():
    return parse_events_page(FIXTURE.read_text())


def test_happy_path_kids_and_families_category_row():
    ev = next(e for e in _events() if e.title == "Stargazing at Floyd Bennett Field")
    assert ev.source == "jbrpc"
    assert ev.venue_name == "Floyd Bennett Field"
    assert ev.borough == Borough.BROOKLYN
    assert ev.start_dt.astimezone(UTC).isoformat() == "2026-07-25T00:00:00+00:00"
    local_start = ev.start_dt.astimezone(NYC_TZ)
    local_end = ev.end_dt.astimezone(NYC_TZ)
    assert (local_start.hour, local_start.minute) == (20, 0)  # 8:00 PM
    assert (local_end.hour, local_end.minute) == (21, 30)  # 9:30 PM
    assert ev.url and ev.url.endswith(
        "/events/2026/4/24/stargazing-at-floyd-bennett-field-hxsx8-pscg9-p9a8r"
    )
    assert ev.external_id == "/events/2026/4/24/stargazing-at-floyd-bennett-field-hxsx8-pscg9-p9a8r"
    assert "nature" in ev.tags
    assert "family" in ev.tags


def test_zero_category_row_kept_via_keyword_fallback():
    # "9th Annual Jamaica Bay Festival" has NO eventlist-cats div at all, but
    # its description says "family-friendly event" — the explicit zero-
    # category decision (see module docstring) keeps it.
    ev = next(e for e in _events() if e.title == "9th Annual Jamaica Bay Festival")
    assert ev.venue_name == "40+ Locations around Jamaica Bay and Rockaway"
    # Genuinely multi-borough venue text — no single answer, falls back to
    # the enrich pass's geocode tiers rather than guessing.
    assert ev.borough is None
    assert ev.price == Price.FREE


def test_zero_category_row_kept_wheel_fun_rentals():
    # Second zero-category fixture row: past card, but the parser itself is
    # date-agnostic (fetch() applies the window filter, not parse_events_page).
    ev = next(
        e
        for e in _events()
        if e.title == "Wheel Fun Rentals Celebrates America's 250th with Patriotic LED Bike Ride"
    )
    assert ev is not None
    assert ev.borough == Borough.BROOKLYN  # "Marine Park Parking Lot"


def test_category_present_but_not_kids_and_families_is_filtered_out():
    # "Rockaway Refresh - Flow and Clean" carries real categories
    # (Stewardship, Refresh) but not "Kids and Families" — a card WITH
    # categories gets no keyword rescue, unlike the zero-category case.
    titles = {e.title for e in _events()}
    assert "Rockaway Refresh - Flow and Clean" not in titles
    assert "International Coastal Cleanup Day at Dubos Point Wildlife Sanctuary" not in titles


def test_queens_venue_via_numbered_beach_street():
    ev = next(e for e in _events() if e.title == "Jamaica Bay Open Paddle Kayaking, B43rd St.")
    assert ev.venue_name == "Beach 43rd Street Parklet"
    assert ev.borough == Borough.QUEENS


def test_queens_venue_via_named_lookup():
    ev = next(
        e
        for e in _events()
        if e.title == "Pollinator Explorers: A JBRPC Family Program at Edgemere Farm"
    )
    assert ev.venue_name == "Edgemere Farm"
    assert ev.borough == Borough.QUEENS
    assert ev.age_min == 2
    assert ev.age_max == 10


def test_org_office_address_fallback_resolves_brooklyn():
    # "Fishing Frenzy at Shirley Chisholm State Park" uses the JBRPC office
    # address as its venue label (an upstream data quirk) — both the office
    # and the park are Brooklyn, so the lookup still resolves correctly.
    ev = next(e for e in _events() if e.title == "Fishing Frenzy at Shirley Chisholm State Park")
    assert ev.venue_name == "Jamaica Bay-Rockaway Parks Conservancy, Inc."
    assert ev.borough == Borough.BROOKLYN
    assert ev.price == Price.FREE


def test_infer_borough_helper():
    assert _infer_borough("Floyd Bennett Field") == Borough.BROOKLYN
    assert _infer_borough("Beach 87th Street Boardwalk (near Rippers)") == Borough.QUEENS
    assert _infer_borough("Gerritsen Ave Beach") == Borough.BROOKLYN
    assert _infer_borough("Some Unknown Place") is None
    assert _infer_borough(None) is None


def test_is_kid_relevant_zero_category_vs_categorized():
    assert _is_kid_relevant("Something", "a fun family day", [])
    assert not _is_kid_relevant("Something", "no relevant words here", [])
    assert _is_kid_relevant("Title", "body", ["Kids and Families"])
    assert not _is_kid_relevant("Title", "family fun for all", ["Stewardship"])
    assert not _is_kid_relevant("Adults Only Night", "21+ event", ["Kids and Families"])
