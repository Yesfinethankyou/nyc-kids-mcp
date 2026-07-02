"""Parser tests for the Prospect Park Alliance source.

Uses the captured fixture (tests/fixtures/prospect_park_sample.json)
and inline dicts. Does not make network calls.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.prospect_park import (
    _infer_tags,
    _is_kid_relevant,
    _parse_cost,
    _parse_row,
    _parse_utc_dt,
    _strip_html,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "prospect_park_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text())["events"]


def _row(**overrides) -> dict:
    """Minimal valid kid-relevant Tribe row for inline tests."""
    base = {
        "id": 12345,
        "title": "Nature Walk for Families",
        "description": "<p>Explore the park.</p>",
        "excerpt": "",
        "categories": [{"name": "Kids"}, {"name": "Nature Programs"}],
        "utc_start_date": "2026-07-01 14:00:00",
        "utc_end_date": "2026-07-01 16:00:00",
        "cost": "Free",
        "url": "https://www.prospectpark.org/event/nature-walk/2026-07-01/",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags():
    assert _strip_html("<p>Hello <strong>world</strong></p>") == "Hello world"


def test_strip_html_decodes_apostrophe_entity():
    # html.unescape decodes &#8217; to the real right single quote (U+2019),
    # not a normalized ASCII apostrophe.
    assert _strip_html("Alfreda&#8217;s Cinema") == "Alfreda’s Cinema"


def test_strip_html_none_returns_empty():
    assert _strip_html(None) == ""


def test_parse_utc_dt_valid():
    assert _parse_utc_dt("2026-06-11 14:00:00") == datetime(2026, 6, 11, 14, 0, 0, tzinfo=UTC)


def test_parse_utc_dt_none():
    assert _parse_utc_dt(None) is None


def test_parse_cost_free():
    assert _parse_cost("Free") == Price.FREE


def test_parse_cost_free_rsvp():
    assert _parse_cost("Free, RSVP!") == Price.FREE


def test_parse_cost_free_rsvp_uppercase():
    assert _parse_cost("FREE, RSVP") == Price.FREE


def test_parse_cost_paid_range():
    assert _parse_cost("$3 – $13") == Price.PAID


def test_parse_cost_prices_vary_is_unknown():
    assert _parse_cost("Prices Vary") == Price.UNKNOWN


def test_parse_cost_empty():
    assert _parse_cost("") == Price.UNKNOWN


def test_parse_cost_none():
    assert _parse_cost(None) == Price.UNKNOWN


# ---------------------------------------------------------------------------
# Category filtering
# ---------------------------------------------------------------------------


def test_kids_category_passes():
    assert _is_kid_relevant(_row(categories=[{"name": "Kids"}])) is True


def test_audubon_category_passes():
    assert _is_kid_relevant(_row(categories=[{"name": "Audubon Center"}])) is True


def test_yoga_only_filtered_out():
    row = _row(title="Prospect Park Yoga", categories=[{"name": "Yoga"}, {"name": "Wellness"}])
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_no_categories_filtered_out():
    assert _is_kid_relevant(_row(categories=[])) is False


def test_hard_exclude_title_overrides_included_category():
    # An included category must not pull back an explicitly adult event.
    row = _row(title="Film Night (21+)", categories=[{"name": "Film"}])
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_members_only_title_hard_excluded():
    row = _row(title="Members Only Carousel Evening", categories=[{"name": "Carousel"}])
    assert _is_kid_relevant(row) is False


def test_fixture_yoga_greenmarket_5k_filtered_out():
    dropped_ids = {10023948, 10000742, 36581}  # Yoga, Greenmarket, Pride 5K
    for row in _load_events():
        if row["id"] in dropped_ids:
            assert _parse_row(row) is None, f"{row['title']} should be filtered out"


# ---------------------------------------------------------------------------
# Happy-path row parse (fixture)
# ---------------------------------------------------------------------------


def test_happy_path_first_event():
    ev = _parse_row(_load_events()[0])
    assert ev is not None
    assert ev.title == "Nature Exploration: Pollinator Month"
    assert ev.source == "prospect_park"
    assert ev.start_dt == datetime(2026, 6, 11, 14, 0, 0, tzinfo=UTC)
    assert ev.end_dt == datetime(2026, 6, 11, 19, 0, 0, tzinfo=UTC)
    assert ev.venue_name == "Prospect Park"
    assert ev.borough == Borough.BROOKLYN
    assert ev.price == Price.FREE
    assert ev.external_id == "10023055"
    assert (
        ev.url
        == "https://www.prospectpark.org/event/nature-exploration-pollinator-month-5/2026-06-11/"
    )
    assert "family" in ev.tags
    assert "nature" in ev.tags
    assert "best for kids" in ev.tags


def test_carousel_event_is_paid():
    row = next(e for e in _load_events() if e["id"] == 10023970)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "Carousel Rides"
    assert ev.price == Price.PAID
    assert "carousel" in ev.tags


def test_film_event_gets_movie_tag_and_decoded_title():
    row = next(e for e in _load_events() if e["id"] == 36922)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.title == "Alfreda’s Cinema, Brooklyn Boheme (2011)"
    assert ev.price == Price.FREE
    assert "movie" in ev.tags


def test_performing_arts_event_with_empty_cost_is_unknown():
    row = next(e for e in _load_events() if e["id"] == 36187)
    ev = _parse_row(row)
    assert ev is not None
    assert ev.price == Price.UNKNOWN
    assert "music" in ev.tags


def test_recurring_occurrences_get_distinct_ids():
    # ids 10023055 / 10023056 are two occurrences of the same recurring event;
    # the Tribe id is per-occurrence, so each must become its own Event.
    events = [e for e in _load_events() if e["id"] in (10023055, 10023056)]
    assert len(events) == 2
    parsed = [_parse_row(e) for e in events]
    assert all(ev is not None for ev in parsed)
    assert parsed[0].external_id != parsed[1].external_id
    assert parsed[0].id != parsed[1].id
    assert parsed[0].title == parsed[1].title


def test_start_dt_is_utc_aware():
    for row in _load_events():
        ev = _parse_row(row)
        if ev is None:
            continue
        assert ev.start_dt.tzinfo is not None
        assert ev.start_dt.utcoffset().total_seconds() == 0


def test_all_events_are_brooklyn_prospect_park():
    for row in _load_events():
        ev = _parse_row(row)
        if ev is not None:
            assert ev.borough == Borough.BROOKLYN
            assert ev.venue_name == "Prospect Park"


def test_event_ids_are_unique():
    parsed = [_parse_row(e) for e in _load_events()]
    ids = [ev.id for ev in parsed if ev is not None]
    assert len(ids) == len(set(ids)), "Duplicate event IDs"


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


def test_missing_cost_parses_as_unknown():
    ev = _parse_row(_row(cost=None))
    assert ev is not None
    assert ev.price == Price.UNKNOWN


def test_missing_start_date_returns_none():
    assert _parse_row(_row(utc_start_date=None)) is None


def test_missing_title_returns_none():
    assert _parse_row(_row(title="")) is None


def test_html_description_stripped_to_plain_text():
    ev = _parse_row(_row(description="<p>Come and <strong>explore</strong> the park!</p>"))
    assert ev is not None
    assert "<" not in (ev.description or "")
    assert "explore" in (ev.description or "")


# ---------------------------------------------------------------------------
# Raw payload
# ---------------------------------------------------------------------------


def test_raw_payload_is_full_upstream_json():
    events = _load_events()
    ev = _parse_row(events[0])
    assert ev is not None
    assert ev.raw_payload is not None
    parsed = json.loads(ev.raw_payload)
    assert parsed["id"] == events[0]["id"]
    assert parsed["categories"] == events[0]["categories"]


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------


def test_infer_tags_always_includes_family():
    assert "family" in _infer_tags("Some Event", set())


def test_infer_tags_from_categories():
    tags = _infer_tags("Open Hours", {"Lefferts Historic House", "Kids"})
    assert "educational" in tags
    assert "best for kids" in tags


def test_infer_tags_from_title_keywords():
    tags = _infer_tags("Juneteenth Film Screening", set())
    assert "movie" in tags
    assert "holiday" in tags
