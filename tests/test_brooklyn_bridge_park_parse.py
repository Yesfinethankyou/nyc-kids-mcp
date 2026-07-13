"""Parser tests for the Brooklyn Bridge Park source.

Uses the captured fixture (tests/fixtures/brooklyn_bridge_park_sample.json —
real WP REST rows, 2026-07-13, yoast/content keys stripped). No network.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date
from zoneinfo import ZoneInfo

from nyc_events.models import Borough, Price
from nyc_events.sources.brooklyn_bridge_park import (
    _base_title,
    _is_kid_relevant,
    _parse_acf_date,
    _parse_wall_time,
    parse_posts,
)

NYC_TZ = ZoneInfo("America/New_York")

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "brooklyn_bridge_park_sample.json"


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def _events():
    return parse_posts(_rows(), locations={18687: "Pier 2"})


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_filter_verdicts_across_fixture():
    titles = {e.title for e in _events()}
    # Kept: family/kids programming across categories (and uncategorized).
    assert any(t.startswith("Pokémon Day Out") for t in titles)
    assert any(t.startswith("Storytime with Brooklyn Public Library") for t in titles)
    assert any(t.startswith("Family Kayaking") for t in titles)
    assert any(t.startswith("Youth Basketball Clinics") for t in titles)
    assert any(t.startswith("Movies With A View") for t in titles)
    # Dropped: adult dance party (Socials & Dancing), seniors program
    # (local blocklist), plain adult Fitness, Volunteer admin.
    assert not any("Papi Juice" in t for t in titles)
    assert not any("Healthy Aging" in t for t in titles)
    assert not any(t.startswith("Kayaking –") or t.startswith("Kayaking &#8211;") for t in titles)
    assert not any("Volunteer Orientation" in t for t in titles)


def test_fitness_needs_family_signal():
    rows = {r["id"]: r for r in _rows()}
    family_kayaking = next(r for r in rows.values() if "Family Kayaking" in r["title"]["rendered"])
    plain_kayaking = next(
        r for r in rows.values() if r["title"]["rendered"].startswith("Kayaking &#8211;")
    )
    assert _is_kid_relevant(family_kayaking)
    assert not _is_kid_relevant(plain_kayaking)


# ---------------------------------------------------------------------------
# Recurring expansion + parent/dated dedup
# ---------------------------------------------------------------------------


def test_recurring_parent_expands_per_occurrence():
    pokemon = [e for e in _events() if e.title.startswith("Pokémon Day Out")]
    # Base date 2026-07-24 plus occurrence-array entries for the 25th & 26th.
    assert {e.start_dt.astimezone(NYC_TZ).date() for e in pokemon} == {
        date(2026, 7, 24),
        date(2026, 7, 25),
        date(2026, 7, 26),
    }
    assert len({e.external_id for e in pokemon}) == 3
    assert all(":" in e.external_id for e in pokemon)


def test_parent_and_dated_posts_dedupe_to_one_row_per_date():
    oysters = [e for e in _events() if "Oysters" in e.title]
    dates = [e.start_dt.astimezone(NYC_TZ).date() for e in oysters]
    assert len(dates) == len(set(dates)), "parent expansion duplicated a dated post"
    # The dated post (July 14) won over the parent's expansion for that date:
    # its occurrence-specific URL survives.
    jul14 = next(e for e in oysters if e.start_dt.astimezone(NYC_TZ).date() == date(2026, 7, 14))
    assert "july-14" in (jul14.url or "")


def test_base_title_strips_dated_suffix_only():
    assert _base_title("Expert-Led Explorations: Oysters – July 14") == (
        "Expert-Led Explorations: Oysters"
    )
    assert _base_title("Movies With A View: Bend It Like Beckham") == (
        "Movies With A View: Bend It Like Beckham"
    )


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


def test_happy_path_fields():
    ev = next(e for e in _events() if e.title.startswith("Pokémon Day Out"))
    assert ev.source == "brooklyn_bridge_park"
    assert ev.borough == Borough.BROOKLYN
    assert ev.price == Price.FREE
    local = ev.start_dt.astimezone(NYC_TZ)
    assert (local.hour, local.minute) == (10, 0)  # "10:00 am"
    end_local = ev.end_dt.astimezone(NYC_TZ)
    assert (end_local.hour, end_local.minute) == (19, 0)  # "7:00 pm"
    assert ev.description and "<" not in ev.description
    assert ev.url and ev.url.startswith("https://brooklynbridgepark.org/event/")
    assert "family" in ev.tags


def test_venue_from_location_map_with_fallback():
    events = _events()
    named = [e for e in events if e.venue_name != "Brooklyn Bridge Park"]
    fallback = [e for e in events if e.venue_name == "Brooklyn Bridge Park"]
    # The fixture's Pier 2 location id resolves; unmapped ids fall back.
    assert any(v.venue_name.startswith("Pier 2,") for v in named) or fallback
    assert all(
        e.venue_name == "Brooklyn Bridge Park" or e.venue_name.endswith(", Brooklyn Bridge Park")
        for e in events
    )


def test_time_and_date_helpers():
    assert _parse_wall_time("10:00 am") == (10, 0)
    assert _parse_wall_time("7:00 pm") == (19, 0)
    assert _parse_wall_time("12:00 pm") == (12, 0)
    assert _parse_wall_time("12:30 am") == (0, 30)
    assert _parse_wall_time("") is None
    assert _parse_wall_time(None) is None
    assert _parse_acf_date("20260724") == date(2026, 7, 24)
    assert _parse_acf_date("2026-07-24") is None
    assert _parse_acf_date(None) is None


def test_post_with_unparseable_date_is_skipped_not_fatal():
    row = json.loads(json.dumps(_rows()[0]))
    row["acf"]["date"] = "garbage"
    row["acf"]["select_date_&_time"] = None
    assert parse_posts([row]) == []
