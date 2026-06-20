"""Parser tests for the Industry City source.

Uses the captured fixture (tests/fixtures/industry_city_sample.json) and
inline dicts. Does not make network calls — the parser takes a dict.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.industry_city import (
    _infer_tags,
    _is_kid_relevant,
    _parse_row,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "industry_city_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text())["events"]


def _row(**overrides) -> dict:
    """Minimal valid kid-relevant Tribe row for inline tests."""
    base = {
        "id": 20324,
        "title": "T-Shirt Yarn Workshop",
        "description": "<p>A hands-on craft workshop for the whole family.</p>",
        "excerpt": "<p>Turn old t-shirts into yarn you can crochet.</p>",
        "categories": [{"name": "Workshops"}],
        "utc_start_date": "2026-06-20 18:00:00",
        "utc_end_date": "2026-06-20 20:00:00",
        "cost": "",
        "url": "https://industrycity.com/event/t-shirt-yarn-workshop/",
        "venue": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Kid-relevance filtering
# ---------------------------------------------------------------------------


def test_workshop_keyword_passes():
    assert _is_kid_relevant(_row()) is True


def test_puppet_kids_show_passes():
    row = _row(
        title="Puppetworks: Behind the Curtain KIDS!",
        description="<p>Children and families enjoy puppet activities and crafts.</p>",
        excerpt="",
        categories=[],
    )
    assert _is_kid_relevant(row) is True


def test_uncategorized_event_can_still_pass_on_keywords():
    # The real kids' puppet show ships with categories=[]; a category-only
    # allowlist would wrongly drop it.
    row = _row(categories=[], title="Family Craft Day", description="", excerpt="")
    assert _is_kid_relevant(row) is True


def test_event_with_no_kid_keyword_filtered_out():
    row = _row(
        title="Outdoor World Cup Watch Party at Industry City 6/20",
        description="<p>Watch the matches on a 15-foot screen. NO STROLLERS.</p>",
        excerpt="",
        categories=[],
    )
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_nightlife_category_hard_excluded():
    # Even if a kid keyword appears, Nightlife wins.
    row = _row(categories=[{"name": "Nightlife"}], title="Family DJ Night")
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_21_plus_blocklist_overrides_allowlist():
    row = _row(
        title="Poppyseeds @ Hifi Provisions",
        description="<p>Garage goth band. 21+ ONLY. A workshop in noise.</p>",
        excerpt="",
        categories=[],
    )
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_alcohol_tour_blocklist_overrides_tour_allowlist():
    # The "gourmet food and drinks tour" matches no allowlist kw on its own,
    # but an alcohol term must drop it even if "tour"/"workshop" appears.
    row = _row(
        title="Father's Day Intro to Sake Class and Brewery Tour",
        description="<p>A guided sake tasting and brewery tour workshop.</p>",
        excerpt="",
        categories=[{"name": "Workshops"}],
    )
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


# ---------------------------------------------------------------------------
# Happy-path row parse (fixture)
# ---------------------------------------------------------------------------


def test_happy_path_yarn_workshop():
    row = next(e for e in _load_events() if e["id"] == 20324)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "T-Shirt Yarn Workshop"
    assert ev.source == "industry_city"
    assert ev.external_id == "20324"
    assert ev.start_dt == datetime(2026, 6, 20, 18, 0, 0, tzinfo=UTC)
    assert ev.end_dt == datetime(2026, 6, 20, 20, 0, 0, tzinfo=UTC)
    assert ev.venue_name == "Industry City"
    assert ev.borough == Borough.BROOKLYN
    assert ev.lat is None
    assert ev.lng is None
    assert ev.age_min is None
    assert ev.age_max is None
    assert ev.price == Price.UNKNOWN
    assert ev.url == "https://industrycity.com/event/t-shirt-yarn-workshop/"
    assert "family" in ev.tags
    assert "arts and crafts" in ev.tags
    assert ev.description is not None


def test_happy_path_puppetworks_kids_from_fixture():
    row = next(e for e in _load_events() if e["id"] == 20388)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "Puppetworks: Behind the Curtain KIDS!"
    assert "best for kids" in ev.tags
    assert "puppets" in ev.tags
    assert ev.borough == Borough.BROOKLYN


def test_entity_decoded_title_from_fixture():
    # Father's Day row uses &#8217; — but it's alcohol content and dropped;
    # the Puppetworks reception uses &#038; and is kept, so assert on it.
    row = next(e for e in _load_events() if e["id"] == 20390)
    ev = _parse_row(row)
    assert ev is not None
    assert "&#038;" not in ev.title
    assert "Reception & Future Home Preview" in ev.title


def test_missing_optional_excerpt_falls_back_to_description():
    # Mending Circle ships excerpt="" with text in description.
    row = next(e for e in _load_events() if e["id"] == 20369)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "Brooklyn Creative Reuse Mending Circle"
    assert ev.description is not None
    assert "mending" in ev.description.lower()


def test_recurring_tour_occurrences_get_distinct_ids():
    # The gourmet tour appears twice with distinct ids + dated URL slugs,
    # confirming external_id = str(id) is per-occurrence. (Both are dropped by
    # the alcohol filter, but the id distinctness is the point being asserted.)
    ids = {e["id"] for e in _load_events() if e["slug"].startswith(
        "industry-city-gourmet-food-and-drinks-tour")}
    assert ids == {10051523, 10051524}


# ---------------------------------------------------------------------------
# Fixture-wide invariants
# ---------------------------------------------------------------------------


def test_fixture_kept_count():
    # Of the 15-row slice: yarn workshop, mending circle, Puppetworks KIDS,
    # and the Puppetworks community reception are kid-relevant; everything else
    # (World Cup watch parties, 21+ band, alcohol tours/classes) is dropped.
    kept = [_parse_row(e) for e in _load_events()]
    kept = [ev for ev in kept if ev is not None]
    titles = sorted(ev.title for ev in kept)
    assert titles == [
        "Brooklyn Creative Reuse Mending Circle",
        "Puppetworks: Behind the Curtain KIDS!",
        "Puppetworks: Behind the Curtain — Community Reception & Future Home Preview",
        "T-Shirt Yarn Workshop",
    ]


def test_all_kept_events_are_brooklyn_unknown_price():
    for row in _load_events():
        ev = _parse_row(row)
        if ev is None:
            continue
        assert ev.venue_name == "Industry City"
        assert ev.borough == Borough.BROOKLYN
        assert ev.price == Price.UNKNOWN


def test_start_dt_is_utc_aware():
    for row in _load_events():
        ev = _parse_row(row)
        if ev is None:
            continue
        assert ev.start_dt.tzinfo is not None
        assert ev.start_dt.utcoffset().total_seconds() == 0


def test_event_ids_are_unique():
    parsed = [_parse_row(e) for e in _load_events()]
    ids = [ev.id for ev in parsed if ev is not None]
    assert ids, "fixture should yield at least one kid-relevant event"
    assert len(ids) == len(set(ids)), "Duplicate event IDs"


def test_raw_payload_is_full_upstream_json():
    row = next(e for e in _load_events() if e["id"] == 20324)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.raw_payload is not None
    parsed = json.loads(ev.raw_payload)
    assert parsed["id"] == row["id"]
    assert parsed["slug"] == row["slug"]


# ---------------------------------------------------------------------------
# Missing optional fields
# ---------------------------------------------------------------------------


def test_missing_end_date_parses_without_error():
    ev = _parse_row(_row(utc_end_date=None))
    assert ev is not None
    assert ev.end_dt is None


def test_missing_url_parses_without_error():
    ev = _parse_row(_row(url=None))
    assert ev is not None
    assert ev.url is None


def test_missing_start_date_returns_none():
    assert _parse_row(_row(utc_start_date=None)) is None


def test_missing_title_returns_none():
    assert _parse_row(_row(title="")) is None


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------


def test_infer_tags_always_includes_family():
    assert "family" in _infer_tags("Some Event", "")


def test_infer_tags_puppets_and_kids():
    tags = _infer_tags("Puppetworks KIDS!", "puppet show for children and families")
    assert "puppets" in tags
    assert "best for kids" in tags


def test_infer_tags_arts_and_crafts():
    tags = _infer_tags("T-Shirt Yarn Workshop", "hands-on craft")
    assert "arts and crafts" in tags
