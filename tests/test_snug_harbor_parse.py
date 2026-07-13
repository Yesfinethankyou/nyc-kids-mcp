"""Parser tests for the Snug Harbor source.

Exercises the pure parser directly against a captured fixture
(`tests/fixtures/snug_harbor_sample.json` — real REST list items paired with
the JSON-LD `Event` dicts extracted from their detail pages, plus the resolved
taxonomy term maps). No network: `parse_event` takes dicts, not responses.
`today` is pinned so the fixture window filter is deterministic.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

from nyc_events.models import Borough, Price, compute_id
from nyc_events.sources.snug_harbor import (
    VENUE_NAME,
    extract_event_jsonld,
    parse_event,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "snug_harbor_sample.json"

# Fixture events span 2026-06-05 .. 2026-08-07. Pin today so the two June
# events fall out of the window and the rest fall in.
TODAY = date(2026, 7, 1)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _terms() -> dict:
    return _fixture()["terms"]


def _events() -> list:
    return _fixture()["events"]


def _parse_all(today: date = TODAY) -> list:
    terms = _terms()
    out = []
    for row in _events():
        ev = parse_event(row["item"], row["jsonld"], terms, today)
        if ev is not None:
            out.append(ev)
    return out


def _by_id(events) -> dict:
    return {e.external_id: e for e in events}


# Term ids from the fixture (stable WP term ids).
COST_FREE, COST_10, COST_PWYW = "21", "23", "22"
AUD_FAMILIES, AUD_ALL_AGES, AUD_KIDS, AUD_TEENS, AUD_ADULTS = "28", "29", "25", "94", "26"


def _item(**overrides) -> dict:
    base = {
        "id": 9001,
        "link": "https://snug-harbor.org/event/example/",
        "title": {"rendered": "Family Farm Workshop"},
        "audience": [int(AUD_FAMILIES)],
        "cost-tier": [int(COST_FREE)],
        "genre": [],
        "program": [],
        "venue": [],
    }
    base.update(overrides)
    return base


def _jsonld(start="2026-07-15T10:00:00-04:00", **overrides) -> dict:
    base = {
        "@type": "Event",
        "name": "Family Farm Workshop | Snug Harbor",
        "startDate": start,
        "endDate": "2026-07-15T12:00:00-04:00",
        "description": "A hands-on farm workshop for families.",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fixture-wide invariants
# ---------------------------------------------------------------------------


def test_fixture_parses_only_in_window_events():
    events = _parse_all()
    # 12 captured rows; the 2026-06-05 and 2026-06-17 events are before TODAY.
    assert len(events) == 10
    for ev in events:
        assert TODAY <= ev.start_dt.date() <= date(2026, 8, 30)


def test_all_events_are_staten_island_snug_harbor():
    for ev in _parse_all():
        assert ev.source == "snug_harbor"
        assert ev.venue_name == VENUE_NAME
        assert ev.borough is Borough.STATEN_ISLAND
        assert ev.start_dt.tzinfo is not None
        assert "family" in ev.tags


def test_event_ids_unique():
    events = _parse_all()
    assert len({e.id for e in events}) == len(events)


def test_external_id_is_post_id_and_compute_id_matches():
    ev = _by_id(_parse_all())["4059"]
    expected = compute_id(
        "snug_harbor", external_id="4059", url=ev.url, title=ev.title, venue=VENUE_NAME
    )
    assert ev.id == expected


# ---------------------------------------------------------------------------
# Window filtering
# ---------------------------------------------------------------------------


def test_past_event_dropped():
    assert parse_event(_item(), _jsonld(start="2026-06-01T10:00:00-04:00"), _terms(), TODAY) is None


def test_far_future_event_dropped():
    assert parse_event(_item(), _jsonld(start="2027-01-01T10:00:00-04:00"), _terms(), TODAY) is None


def test_no_jsonld_dropped():
    assert parse_event(_item(), None, _terms(), TODAY) is None


def test_missing_start_date_dropped():
    assert parse_event(_item(), _jsonld(start=None), _terms(), TODAY) is None


# ---------------------------------------------------------------------------
# Happy-path field mapping (from the fixture)
# ---------------------------------------------------------------------------


def test_happy_path_fields():
    ev = _by_id(_parse_all())["4059"]
    assert ev.title == "Farm Cooking Club with Pam Silvestri: Herb Fritters"
    assert ev.url == "https://snug-harbor.org/event/farm-cooking-club-with-pam-silvestri-herb-fritters/"
    assert ev.start_dt.date() == date(2026, 7, 18)
    assert ev.start_dt.hour == 10 and ev.start_dt.minute == 30
    assert ev.end_dt is not None and ev.end_dt.hour == 12
    assert ev.description and "herb" in ev.description.lower()
    # cost-tier "$10 & Under" -> PAID
    assert ev.price is Price.PAID


def test_title_uses_clean_rest_title_not_jsonld_suffix():
    # JSON-LD name has " | Snug Harbor"; the REST title.rendered does not.
    ev = _by_id(_parse_all())["4059"]
    assert "| Snug Harbor" not in ev.title


# ---------------------------------------------------------------------------
# Price mapping
# ---------------------------------------------------------------------------


def test_price_free():
    ev = parse_event(_item(**{"cost-tier": [int(COST_FREE)]}), _jsonld(), _terms(), TODAY)
    assert ev.price is Price.FREE


def test_price_paid_from_under_tier():
    ev = parse_event(_item(**{"cost-tier": [int(COST_10)]}), _jsonld(), _terms(), TODAY)
    assert ev.price is Price.PAID


def test_price_pay_what_you_wish_is_unknown():
    ev = parse_event(_item(**{"cost-tier": [int(COST_PWYW)]}), _jsonld(), _terms(), TODAY)
    assert ev.price is Price.UNKNOWN


def test_price_no_tier_is_unknown():
    ev = parse_event(_item(**{"cost-tier": []}), _jsonld(), _terms(), TODAY)
    assert ev.price is Price.UNKNOWN


# ---------------------------------------------------------------------------
# Tagging: audience -> "best for kids"
# ---------------------------------------------------------------------------


def test_best_for_kids_tag_when_family_audience():
    ev = parse_event(_item(audience=[int(AUD_FAMILIES)]), _jsonld(), _terms(), TODAY)
    assert "best for kids" in ev.tags


def test_no_best_for_kids_tag_when_teens_only():
    # Teens is kept (still youth programming) but doesn't earn "best for kids".
    ev = parse_event(
        _item(audience=[int(AUD_TEENS), int(AUD_ADULTS)]), _jsonld(), _terms(), TODAY
    )
    assert ev is not None
    assert "best for kids" not in ev.tags
    assert "family" in ev.tags


def test_genre_maps_to_tags():
    # genre 54 = Cooking -> "educational"; 17 = Dance -> "theater".
    ev = parse_event(_item(genre=[54, 17]), _jsonld(), _terms(), TODAY)
    assert "educational" in ev.tags
    assert "theater" in ev.tags


# ---------------------------------------------------------------------------
# Adult safety-net filter (on top of the audience gate)
# ---------------------------------------------------------------------------


def test_adult_title_dropped():
    item = _item(title={"rendered": "Annual Gala Fundraiser (21+)"})
    assert parse_event(item, _jsonld(), _terms(), TODAY) is None


def test_members_only_title_dropped():
    item = _item(title={"rendered": "Members Only Preview Reception"})
    assert parse_event(item, _jsonld(), _terms(), TODAY) is None


# ---------------------------------------------------------------------------
# JSON-LD extraction helper
# ---------------------------------------------------------------------------


def test_extract_event_jsonld_from_graph():
    html = (
        '<html><head>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"WebPage","name":"x"},'
        '{"@type":"Event","name":"E","startDate":"2026-07-15T10:00:00-04:00"}]}'
        '</script></head><body></body></html>'
    )
    node = extract_event_jsonld(html)
    assert node is not None
    assert node["@type"] == "Event"
    assert node["startDate"].startswith("2026-07-15")


def test_extract_event_jsonld_absent_returns_none():
    html = '<script type="application/ld+json">{"@type":"WebPage"}</script>'
    assert extract_event_jsonld(html) is None


# ---------------------------------------------------------------------------
# DB boundary (tz-aware datetimes survive the upsert)
# ---------------------------------------------------------------------------


def test_events_survive_db_upsert(tmp_path):
    from nyc_events import db

    events = _parse_all()
    db_path = str(tmp_path / "events.db")
    db.init_events(db_path)
    with db.connect_events(db_path) as conn:
        ins, _ = db.upsert_events(conn, events)
        assert ins == len(events)
        stored = db.search(conn, limit=100)
    assert len(stored) == len(events)
