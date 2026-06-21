"""Unit tests for the shared kid-relevance filter helpers."""

from __future__ import annotations

from nyc_events.sources._filters import (
    ADULT_BLOCKLIST,
    ADULT_TITLE_BLOCKLIST,
    MEMBERS_ONLY,
    contains_any,
    normalize,
)


def test_normalize_lowercases_and_collapses_hyphens_and_whitespace():
    assert normalize("Adults-Only") == "adults only"
    assert normalize("after   party") == "after party"
    assert normalize("Members-Only\n Tour") == "members only tour"
    assert normalize(None) == ""
    assert normalize("") == ""


def test_contains_any_matches_hyphen_and_space_variants():
    # A single canonical spelling matches all variants thanks to normalize().
    assert contains_any("This event is Adults-Only", ["adults only"])
    assert contains_any("ADULTS  ONLY please", ["adults only"])
    assert not contains_any("Family Day", ["adults only"])


def test_contains_any_handles_missing_text():
    assert not contains_any(None, ADULT_BLOCKLIST)
    assert not contains_any("", ADULT_BLOCKLIST)


def test_adults_only_substring_covers_for_adults_only():
    # "for adults only" needs no separate entry — "adults only" is a substring.
    assert contains_any("Tour (For Adults Only)", ADULT_BLOCKLIST)


def test_adult_blocklist_flags_core_signals():
    for text in ["21+ show", "18+ night", "burlesque revue",
                 "no children allowed"]:
        assert contains_any(text, ADULT_BLOCKLIST), text


def test_drag_is_title_only_not_in_core_blocklist():
    # drag show/brunch live in the title-only set so a body mention of an
    # adjacent drag show doesn't drop a family event.
    assert contains_any("Drag Show Brunch", ADULT_TITLE_BLOCKLIST)
    assert contains_any("Family Drag Brunch", ADULT_TITLE_BLOCKLIST)
    assert not contains_any("drag show", ADULT_BLOCKLIST)


def test_members_only_is_separate_from_adult_content():
    assert contains_any("Birding (Members-Only)", MEMBERS_ONLY)
    assert not contains_any("Birding (Members-Only)", ADULT_BLOCKLIST)
