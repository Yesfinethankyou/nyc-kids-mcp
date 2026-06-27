"""Neighborhood coding: static venue->neighborhood tables + the NTA crosswalk.

Shared, source-agnostic helpers (sibling to _filters.py). The enrichment pass
(`nyc_events.enrich`) layers these deterministic lookups *under* a geocoding
fallback:

    Tier 1  fixed-venue source         -> SOURCE_NEIGHBORHOOD
    Tier 2  enumerable multi-site       -> VENUE_NEIGHBORHOOD
    Tier 3  permit/other park name      -> park_neighborhoods.json
    Tier 4  reverse-geocode lat/lng     -> nta_for_tract  (enrich.py, network)
    Tier 5  forward-geocode venue       -> nta_for_tract  (enrich.py, network)

Everything in this module is pure and offline. The two JSON tables are built
once from NYC open data by scripts/build_tract_nta.py and
scripts/build_park_neighborhoods.py and shipped as package data.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

# Tier 1: single fixed-venue sources -> their neighborhood. Labels are chosen
# to be substrings of the official NTA names where possible (e.g. "Sunset
# Park", "Crown Heights") so the search filter unifies these curated rows with
# the geocode-derived rows that carry full NTA names.
SOURCE_NEIGHBORHOOD: dict[str, str] = {
    "bk_childrens_museum": "Crown Heights",
    "brooklyn_army_terminal": "Sunset Park",
    "domino_park": "Williamsburg",
    "greenwood_cemetery": "Greenwood Heights",
    "industry_city": "Sunset Park",
    "prospect_park": "Prospect Park",
    "governors_island": "Governors Island",
}

# Tier 2: multi-site sources small enough to enumerate. NY Transit Museum runs
# the main museum (Downtown Brooklyn, Brooklyn Heights border) and a Grand
# Central gallery annex in Midtown Manhattan. Keyed on (source, normalized
# venue name).
VENUE_NEIGHBORHOOD: dict[tuple[str, str], str] = {
    ("ny_transit_museum", "new york transit museum"): "Brooklyn Heights",
    ("ny_transit_museum", "ny transit museum"): "Brooklyn Heights",
    ("ny_transit_museum", "grand central terminal"): "Midtown",
    ("ny_transit_museum", "ny transit museum gallery annex store"): "Midtown",
    ("ny_transit_museum", "gallery annex"): "Midtown",
}

_NONWORD = re.compile(r"[^a-z0-9]+")

# Generic library-name tokens stripped to a "core" so the BPL feed's
# "Arlington Library" / "Central Library, Info Commons" key the same as FacDB's
# "ARLINGTON LIBRARY" / "CENTRAL LIBRARY". See build_library_neighborhoods.py.
_LIBRARY_TOKENS = re.compile(r"\b(library|branch|info commons|learning center)\b")


def normalize_name(s: str | None) -> str:
    """Lowercase, strip punctuation to spaces, collapse whitespace. Used to
    key both the park table (build + lookup) and VENUE_NEIGHBORHOOD so that
    "Sara D. Roosevelt Park" and "sara d roosevelt park" match."""
    if not s:
        return ""
    return _NONWORD.sub(" ", s.lower()).strip()


def library_core(s: str | None) -> str:
    """normalize_name minus the generic library tokens, re-collapsed."""
    return _NONWORD.sub(" ", _LIBRARY_TOKENS.sub(" ", normalize_name(s))).strip()


def _load_json(name: str) -> dict[str, str]:
    # Defensive: the build scripts may not have run yet (e.g. fresh checkout
    # before data-prep). Treat a missing table as empty rather than crashing
    # import — neighborhood just stays None, the existing status quo.
    try:
        with resources.files("nyc_events.data").joinpath(name).open(encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return {}


@lru_cache(maxsize=1)
def _park_table() -> dict[str, str]:
    return _load_json("park_neighborhoods.json")


@lru_cache(maxsize=1)
def _library_table() -> dict[str, str]:
    return _load_json("library_neighborhoods.json")


@lru_cache(maxsize=1)
def _tract_table() -> dict[str, str]:
    return _load_json("tract_to_nta.json")


def static_neighborhood(source: str, venue: str | None, borough: str | None = None) -> str | None:
    """Tiers 1-3: deterministic, no network. Returns a neighborhood or None."""
    if source in SOURCE_NEIGHBORHOOD:
        return SOURCE_NEIGHBORHOOD[source]
    nv = normalize_name(venue)
    if (source, nv) in VENUE_NEIGHBORHOOD:
        return VENUE_NEIGHBORHOOD[(source, nv)]
    # Library branches: keyed by (borough, library-core). Gated on the venue
    # actually being a library so a park like "Sunset Park" can't collide with
    # the "Sunset Park Library" entry.
    if "library" in nv.split():
        lib = _library_table().get(f"{normalize_name(borough)}|{library_core(venue)}")
        if lib:
            return lib
    return _park_table().get(nv) or None


def nta_for_tract(geoid: str | None) -> str | None:
    """Map an 11-digit 2020 census tract GEOID to its NTA neighborhood name."""
    if not geoid:
        return None
    return _tract_table().get(geoid)
