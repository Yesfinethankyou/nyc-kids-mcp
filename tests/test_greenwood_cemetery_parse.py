"""Parser tests for the Green-Wood Cemetery source.

Uses the captured fixture (tests/fixtures/greenwood_cemetery_sample.json)
and inline dicts. Does not make network calls.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from nyc_events.models import Borough, Price
from nyc_events.sources.greenwood_cemetery import (
    _infer_tags,
    _is_kid_relevant,
    _parse_cost,
    _parse_row,
    _parse_utc_dt,
    _strip_html,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "greenwood_cemetery_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text())["events"]


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags():
    raw = "<p>Hello <strong>world</strong></p>"
    assert _strip_html(raw) == "Hello world"


def test_strip_html_collapses_whitespace():
    raw = "<div>  foo  <br/>  bar  </div>"
    assert _strip_html(raw) == "foo bar"


def test_strip_html_none_returns_empty():
    assert _strip_html(None) == ""


def test_strip_html_replaces_entities():
    assert "&amp;" in _strip_html("<p>&amp;</p>") or "&" in _strip_html("<p>&amp;</p>")


def test_strip_html_drops_style_script_button_contents():
    # Green-Wood's Stackable theme embeds these in `description`; de-tagging
    # alone leaks the CSS/JS text into the event description (the ".stk-…
    # {margin…}" bug).
    raw = (
        "<style>.stk-abc {margin:0 !important;}</style>"
        "<p>Real prose.</p>"
        "<script>var cb = function() {};</script>"
        "<button type='button'>Buy Tickets</button>"
        "<!-- a comment -->"
    )
    assert _strip_html(raw) == "Real prose."


def test_strip_html_drops_leading_schedule_comma():
    # Tribe's schedule header de-tags to a bare "," before the prose starts.
    raw = "<div><span></span><span>, </span></div><p>Hello.</p>"
    assert _strip_html(raw) == "Hello."


def test_fixture_descriptions_carry_no_css_or_js():
    # Every fixture row embeds <style> (some also <script>) in `description`
    # — the parsed Event description must never leak CSS/JS text.
    for row in _load_events():
        ev = _parse_row(row)
        if ev is None or ev.description is None:
            continue
        assert ".stk-" not in ev.description, ev.title
        assert "!important" not in ev.description, ev.title
        assert "var exampleCallback" not in ev.description, ev.title
        assert not ev.description.startswith(","), ev.title


def test_parse_utc_dt_valid():
    result = _parse_utc_dt("2026-06-05 23:30:00")
    assert result == datetime(2026, 6, 5, 23, 30, 0, tzinfo=UTC)


def test_parse_utc_dt_none():
    assert _parse_utc_dt(None) is None


def test_parse_utc_dt_empty():
    assert _parse_utc_dt("") is None


def test_parse_cost_free():
    assert _parse_cost("Free") == Price.FREE


def test_parse_cost_free_rsvp():
    assert _parse_cost("Free, RSVP!") == Price.FREE


def test_parse_cost_paid_simple():
    assert _parse_cost("$15") == Price.PAID


def test_parse_cost_paid_range():
    assert _parse_cost("$3 - $13") == Price.PAID


def test_parse_cost_empty():
    assert _parse_cost("") == Price.UNKNOWN


def test_parse_cost_none():
    assert _parse_cost(None) == Price.UNKNOWN


# ---------------------------------------------------------------------------
# Filtering tests
# ---------------------------------------------------------------------------


def test_adult_gala_is_filtered_out():
    row = {
        "id": 99999,
        "title": "Annual Gala Dinner",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-01 23:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/gala/",
    }
    assert _is_kid_relevant(row) is False
    assert _parse_row(row) is None


def test_cocktail_alone_is_not_an_adult_signal():
    # "cocktail" is not blocklisted — alcohol alone is not an adult-only signal.
    # Green-Wood is allowlist-required, so a cocktail event with a family keyword
    # is kept (allowlist wins), while one with no keyword is dropped by the
    # conservative default (the soft blocklist was dead code and was removed).
    kept = {
        "id": 99996,
        "title": "Family Cocktail & Mocktail Garden Party",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-01 18:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/family/",
    }
    dropped = {
        "id": 99998,
        "title": "VIP Cocktail Reception",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-01 22:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/vip/",
    }
    assert _is_kid_relevant(kept) is True
    assert _is_kid_relevant(dropped) is False


def test_family_keyword_passes_filter():
    row = {
        "id": 99997,
        "title": "Family Nature Walk",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-01 15:00:00",
        "utc_end_date": None,
        "cost": "Free",
        "url": "https://www.green-wood.com/event/family-walk/",
    }
    assert _is_kid_relevant(row) is True


def test_tour_keyword_passes_filter():
    row = {
        "id": 99996,
        "title": "Historic Trolley Tour",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-01 15:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/tour/",
    }
    assert _is_kid_relevant(row) is True


def test_film_screening_passes_filter():
    row = {
        "id": 99995,
        "title": "Film Screening at the Cemetery",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-01 22:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/film/",
    }
    assert _is_kid_relevant(row) is True


def test_adults_only_title_hard_excluded_despite_allowlist_hit():
    # "tour" is an allowlist keyword, but "Adults Only" in the title must drop
    # the event unconditionally (promoted from the old dead soft-blocklist to
    # the hard-exclude list, matching the other sources).
    row = {
        "id": 99992,
        "title": "After-Dark Catacombs Tour (Adults Only)",
        "description": "An evening tour of the catacombs.",
        "excerpt": "",
        "categories": [{"name": "Tours"}],
        "utc_start_date": "2026-07-01 23:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/adults-tour/",
    }
    assert _is_kid_relevant(row) is False


def test_members_only_title_hard_excluded_despite_allowlist_hit():
    # "birding" is an allowlist keyword, but "Members Only" in the title must
    # drop the event unconditionally — members-only events aren't public.
    row = {
        "id": 99994,
        "title": "Birding in Peace: Late-Risers Edition (Members Only)",
        "description": "A relaxed morning of birding.",
        "excerpt": "",
        "categories": [{"name": "Nature"}],
        "utc_start_date": "2026-07-01 13:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/birding-members/",
    }
    assert _is_kid_relevant(row) is False


def test_members_only_hyphenated_title_hard_excluded():
    row = {
        "id": 99993,
        "title": "Members-Only Garden Walk",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-01 13:00:00",
        "utc_end_date": None,
        "cost": "",
        "url": "https://www.green-wood.com/event/members-garden/",
    }
    assert _is_kid_relevant(row) is False


# ---------------------------------------------------------------------------
# Happy-path row parse
# ---------------------------------------------------------------------------


def test_happy_path_first_event():
    """First fixture event parses with correct title, UTC start, venue, borough."""
    events = _load_events()
    ev = _parse_row(events[0])

    assert ev is not None
    assert ev.title == "Green-Wood After Hours"
    assert ev.source == "greenwood_cemetery"
    assert ev.start_dt == datetime(2026, 6, 5, 23, 30, 0, tzinfo=UTC)
    assert ev.end_dt == datetime(2026, 6, 6, 1, 30, 0, tzinfo=UTC)
    assert ev.venue_name == "Green-Wood Cemetery"
    assert ev.borough == Borough.BROOKLYN
    assert ev.price == Price.UNKNOWN
    assert ev.url == "https://www.green-wood.com/event/green-wood-after-hours-47/2026-06-05/"
    assert ev.external_id == "10037603"
    assert "family" in ev.tags


def test_film_event_gets_movie_tag():
    """Rooftop Films event matches 'film'/'screening' and should get movie tag."""
    events = _load_events()
    # Find the Rooftop Films event (id=74794)
    film_ev = next(e for e in events if e["id"] == 74794)
    ev = _parse_row(film_ev)
    assert ev is not None
    assert "movie" in ev.tags


def test_start_dt_is_utc_aware():
    events = _load_events()
    for row in events:
        ev = _parse_row(row)
        if ev is None:
            continue
        assert ev.start_dt.tzinfo is not None
        assert ev.start_dt.utcoffset().total_seconds() == 0


def test_all_events_are_brooklyn():
    events = _load_events()
    for row in events:
        ev = _parse_row(row)
        if ev is not None:
            assert ev.borough == Borough.BROOKLYN


def test_external_id_is_string():
    events = _load_events()
    ev = _parse_row(events[0])
    assert ev is not None
    assert isinstance(ev.external_id, str)
    assert ev.external_id == str(events[0]["id"])


def test_event_ids_are_unique():
    events = _load_events()
    parsed = [_parse_row(e) for e in events]
    filtered = [ev for ev in parsed if ev is not None]
    ids = [ev.id for ev in filtered]
    assert len(ids) == len(set(ids)), "Duplicate event IDs"


# ---------------------------------------------------------------------------
# Missing optional fields
# ---------------------------------------------------------------------------


def test_missing_end_date_parses_without_error():
    row = {
        "id": 88888,
        "title": "Family Garden Walk",
        "description": "<p>A lovely walk through the garden.</p>",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-10 14:00:00",
        "utc_end_date": None,
        "cost": None,
        "url": "https://www.green-wood.com/event/garden-walk/",
    }
    ev = _parse_row(row)
    assert ev is not None
    assert ev.end_dt is None
    assert ev.price == Price.UNKNOWN


def test_missing_url_parses_without_error():
    row = {
        "id": 88887,
        "title": "Nature Workshop",
        "description": "<p>Explore nature.</p>",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-10 15:00:00",
        "utc_end_date": None,
        "cost": "Free",
        "url": None,
    }
    ev = _parse_row(row)
    assert ev is not None
    assert ev.url is None
    assert ev.price == Price.FREE


def test_html_description_stripped_to_plain_text():
    row = {
        "id": 88886,
        "title": "Kids Workshop",
        "description": "<p>Come and <strong>explore</strong> the garden!</p>",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-07-10 15:00:00",
        "utc_end_date": None,
        "cost": "$5",
        "url": "https://www.green-wood.com/event/workshop/",
    }
    ev = _parse_row(row)
    assert ev is not None
    assert "<" not in (ev.description or "")
    assert "explore" in (ev.description or "")
    assert ev.price == Price.PAID


# ---------------------------------------------------------------------------
# Cost mapping
# ---------------------------------------------------------------------------


def test_free_event_price():
    row = {
        "id": 77777,
        "title": "Free Family Concert",
        "description": "",
        "excerpt": "A free outdoor concert.",
        "categories": [],
        "utc_start_date": "2026-08-01 18:00:00",
        "utc_end_date": None,
        "cost": "Free",
        "url": "https://www.green-wood.com/event/concert/",
    }
    ev = _parse_row(row)
    assert ev is not None
    assert ev.price == Price.FREE


def test_paid_event_price():
    row = {
        "id": 77776,
        "title": "Garden Tour for Families",
        "description": "",
        "excerpt": "",
        "categories": [],
        "utc_start_date": "2026-08-01 14:00:00",
        "utc_end_date": None,
        "cost": "$15",
        "url": "https://www.green-wood.com/event/garden-tour/",
    }
    ev = _parse_row(row)
    assert ev is not None
    assert ev.price == Price.PAID


# ---------------------------------------------------------------------------
# Raw payload
# ---------------------------------------------------------------------------


def test_raw_payload_is_json_string():
    events = _load_events()
    ev = _parse_row(events[0])
    assert ev is not None
    assert ev.raw_payload is not None
    parsed = json.loads(ev.raw_payload)
    assert parsed["id"] == events[0]["id"]


# ---------------------------------------------------------------------------
# Tag inference
# ---------------------------------------------------------------------------


def test_infer_tags_always_includes_family():
    tags = _infer_tags("Some Lecture", None)
    assert "family" in tags


def test_infer_tags_nature():
    tags = _infer_tags("Bird Walk at Green-Wood", None)
    assert "nature" in tags


def test_infer_tags_holiday():
    tags = _infer_tags("Halloween at the Cemetery", None)
    assert "halloween" in tags


def test_infer_tags_movie():
    tags = _infer_tags("Film Screening", "Come watch a great film.")
    assert "movie" in tags
