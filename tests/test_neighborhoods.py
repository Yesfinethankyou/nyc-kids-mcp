"""Static neighborhood lookups (sources/_neighborhoods.py).

Deterministic, offline. Exercises the three no-network tiers plus the tract
crosswalk against the shipped data tables.
"""

from __future__ import annotations

from nyc_events.sources import _neighborhoods as nb


def test_normalize_name_collapses_punctuation_and_case():
    assert nb.normalize_name("Sara D. Roosevelt Park") == "sara d roosevelt park"
    assert nb.normalize_name("  Randall's   Island Park ") == "randall s island park"
    assert nb.normalize_name(None) == ""
    assert nb.normalize_name("") == ""


def test_tier1_fixed_venue_sources_resolve():
    # The venue arg is ignored for fixed-venue sources.
    assert nb.static_neighborhood("domino_park", None) == "Williamsburg"
    assert nb.static_neighborhood("industry_city", "whatever") == "Sunset Park"
    assert nb.static_neighborhood("bk_childrens_museum", None) == "Crown Heights"
    assert nb.static_neighborhood("prospect_park", None) == "Prospect Park"


def test_tier2_enumerated_multisite_resolve():
    transit = "ny_transit_museum"
    assert nb.static_neighborhood(transit, "New York Transit Museum") == "Brooklyn Heights"
    assert nb.static_neighborhood(transit, "Grand Central Terminal") == "Midtown"
    # An unknown transit venue is not in the dict and has no park match -> None.
    assert nb.static_neighborhood("ny_transit_museum", "Somewhere Unknown") is None


def test_tier3_park_table_resolves_permit_parks():
    # Big destination parks are their own NTA; this exercises the open-data
    # table built by scripts/build_park_neighborhoods.py.
    assert nb.static_neighborhood("nyc_permitted_events", "Prospect Park") == "Prospect Park"
    assert nb.static_neighborhood("nyc_permitted_events", "Cunningham Park") == "Cunningham Park"
    # Normalization means punctuation variants still hit.
    assert nb.static_neighborhood("nyc_permitted_events", "cunningham   park") == "Cunningham Park"


def test_unmatched_returns_none():
    assert nb.static_neighborhood("nyc_permitted_events", "Not A Real Park 9000") is None
    assert nb.static_neighborhood("mommy_poppins", None) is None


def test_nta_for_tract_crosswalk():
    # Brooklyn Children's Museum tract -> its NTA.
    assert nb.nta_for_tract("36047034100") == "Crown Heights (North)"
    assert nb.nta_for_tract(None) is None
    assert nb.nta_for_tract("99999999999") is None


def test_data_tables_are_populated():
    # Guard against shipping empty tables (a broken data-prep run).
    assert len(nb._park_table()) > 1000
    assert len(nb._tract_table()) > 2000
