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
    assert nb.static_neighborhood("snug_harbor", "Great Hall") == "Snug Harbor"


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


def test_library_core_strips_generic_tokens():
    assert nb.library_core("Arlington Library") == "arlington"
    assert nb.library_core("Central Library, Info Commons") == "central"
    assert nb.library_core("Library for Arts & Culture") == "for arts culture"


def test_library_tier_codes_bpl_branches_by_borough():
    # Built from FacDB; gated on the venue actually being a library.
    assert nb.static_neighborhood("bpl", "Sunset Park Library", "Brooklyn") == "Sunset Park (West)"
    assert nb.static_neighborhood("bpl", "Greenpoint Library", "Brooklyn") == "Greenpoint"
    # Wrong borough doesn't match (borough-keyed, collision-safe).
    assert nb.static_neighborhood("bpl", "Sunset Park Library", "Queens") is None


def test_library_gate_excludes_non_library_venues():
    # A park named "Sunset Park" must not borrow the library entry — it has no
    # "library" token, so it routes to the park table instead.
    park = nb.static_neighborhood("nyc_permitted_events", "Sunset Park", "Brooklyn")
    assert park == "Sunset Park (West)"


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
    assert len(nb._library_table()) > 100
    assert len(nb._tract_table()) > 2000
