"""Parser tests for the NYC Parks website events source.

Exercises the parser directly against the captured fixture (no httpx mock).
Fixture is a real excerpt of https://www.nycgovparks.org/events/kids page 1,
captured 2026-07-06: 10 microdata Event cards, the eventsByLocationJSON blob
trimmed to its first 6 venues (280 event links), and the pagination markup.

9 of the 10 cards join the trimmed blob; the "Kids in Motion: Anne Loftus
Playground" card's venue (Fort Tryon Park) was trimmed out, making it the
natural no-join fallback case.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nyc_events.models import Borough, Price
from nyc_events.sources.nycgovparks_events import (
    parse_location_blob,
    parse_page,
)

FIXTURE = Path(__file__).parent / "fixtures" / "nycgovparks_events_kids_page.html"


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text()


@pytest.fixture(scope="module")
def link_map(html: str):
    return parse_location_blob(html)


@pytest.fixture(scope="module")
def parsed(html: str, link_map):
    return parse_page(html, link_map)


@pytest.fixture(scope="module")
def events(parsed):
    return parsed[0]


@pytest.fixture(scope="module")
def by_id(events):
    return {e.external_id: e for e in events}


# --- Blob parsing -------------------------------------------------------------


def test_blob_has_six_venues_worth_of_links(link_map):
    # Trimmed blob: 6 venues / 280 per-occurrence event links.
    assert len(link_map) == 280
    assert len({v.name for v in link_map.values()}) == 6


def test_blob_venue_fields(link_map):
    info = link_map["/events/2026/07/06/alfred-e-smith-recreation-center-summer-camp-extended-day"]
    assert info.name == "Alfred E. Smith Recreation Center"
    assert info.borough == "Manhattan"
    assert info.lat == pytest.approx(40.71032072)
    assert info.lng == pytest.approx(-73.99763525)


def test_blob_missing_returns_empty(caplog):
    assert parse_location_blob("<html><body>no blob here</body></html>") == {}


# --- Card parsing: happy path ---------------------------------------------------


def test_all_ten_cards_parsed(parsed):
    events, n_cards = parsed
    assert n_cards == 10
    assert len(events) == 10  # nothing filtered on the real fixture


def test_happy_path_row(by_id):
    ev = by_id["2205424"]
    assert ev.source == "nycgovparks_events"
    assert ev.title == "Alfred E. Smith Recreation Center Summer Camp Extended Day"
    assert ev.url == (
        "https://www.nycgovparks.org/events/2026/07/06/"
        "alfred-e-smith-recreation-center-summer-camp-extended-day"
    )
    # meta startDate is full ISO-8601 with offset — parsed exactly.
    assert ev.start_dt == datetime.fromisoformat("2026-07-06T08:00:00-04:00")
    assert ev.end_dt == datetime.fromisoformat("2026-07-06T18:00:00-04:00")
    assert ev.borough is Borough.MANHATTAN
    assert ev.price is Price.FREE
    assert ev.description and ev.description.startswith("The NYC Parks Summer Day Camp")
    assert ev.neighborhood is None  # enrich pass codes it


def test_blob_join_attaches_lat_lng(by_id):
    ev = by_id["2205424"]
    assert ev.lat == pytest.approx(40.71032072)
    assert ev.lng == pytest.approx(-73.99763525)


def test_blob_parent_venue_preferred_over_subroom(by_id):
    # Microdata Place says "Multi-Use Room (in Alfred E. Smith Recreation
    # Center)"; the blob's top-level name wins.
    assert by_id["2205424"].venue_name == "Alfred E. Smith Recreation Center"


def test_blob_venue_is_park_property(by_id):
    # "Kids In Motion: Addabbo Playground" — Place is "Addabbo Playground
    # (in Tudor Park)", blob keys the park property "Tudor Park", which is
    # what lines up with the park_neighborhoods.json enrich tier.
    ev = by_id["2207000"]
    assert ev.venue_name == "Tudor Park"
    assert ev.borough is Borough.QUEENS
    assert ev.lat == pytest.approx(40.673348, abs=1e-5)


def test_boroughs_from_address_locality(by_id):
    assert by_id["2181768"].borough is Borough.STATEN_ISLAND
    assert by_id["2182028"].borough is Borough.BRONX


def test_category_ids_mapped_to_tags(by_id):
    # Foragers in the Foodway: cat5 cat18 cat28 cat29 cat303
    # -> educational, best for kids, tour, volunteer, gardening.
    ev = by_id["2182028"]
    assert ev.tags[:2] == ["family", "best for kids"]
    assert set(ev.tags) == {
        "family", "best for kids", "educational", "tour", "volunteer", "gardening",
    }


def test_unknown_category_ids_skipped(by_id):
    # Summer camp card is cat18 cat205 cat211; 205/211 are unmapped internal
    # markers — only the seeded tags remain.
    assert by_id["2205424"].tags == ["family", "best for kids"]


def test_all_fixture_rows_free(events):
    # Every kids-category card on the captured page carries the "Free!" line.
    assert all(e.price is Price.FREE for e in events)


def test_raw_payload_is_trimmed_extract(by_id):
    import json

    raw = json.loads(by_id["2181768"].raw_payload)
    assert raw["event_id"] == "2181768"
    assert raw["category_ids"] == [18, 25, 137, 205, 291]
    assert raw["place_name"] == "Greenbelt Recreation Center"
    assert raw["address_locality"] == "Staten Island"
    assert raw["accessible"] is True
    assert raw["pearls_pick"] is True
    assert raw["map_venue"]["name"] == "Greenbelt Recreation Center"
    # It's a structured extract, never the HTML blob.
    assert "<div" not in by_id["2181768"].raw_payload


# --- Missing optional fields (no blob join) -------------------------------------


def test_no_join_row_falls_back_gracefully(by_id):
    # Anne Loftus Playground's venue (Fort Tryon Park) isn't in the trimmed
    # blob: no lat/lng, venue falls back to the "(in <parent>)" park name,
    # borough still comes from addressLocality.
    ev = by_id["2192210"]
    assert ev.lat is None
    assert ev.lng is None
    assert ev.venue_name == "Fort Tryon Park"
    assert ev.borough is Borough.MANHATTAN


def test_parse_without_blob_map_at_all(html):
    # Even with an empty link map every card still parses (venue from
    # microdata, no coords).
    events, n_cards = parse_page(html, {})
    assert n_cards == 10
    assert len(events) == 10
    assert all(e.lat is None and e.lng is None for e in events)
    # Place without a "(in …)" parent falls back to the Place name itself.
    concrete = next(e for e in events if e.external_id == "2182028")
    assert concrete.venue_name == "Concrete Plant Park"


# --- Skip rules ------------------------------------------------------------------


def test_cancelled_title_skipped(html, link_map):
    mutated = html.replace(
        ">Foragers in the Foodway</a>", ">CANCELLED: Foragers in the Foodway</a>"
    )
    events, n_cards = parse_page(mutated, link_map)
    assert n_cards == 10  # the card still counts toward the pagination terminator
    assert len(events) == 9
    assert all(not e.title.lower().startswith("cancelled") for e in events)


def test_adult_blocklist_safety_net(html, link_map):
    mutated = html.replace(
        ">Foragers in the Foodway</a>", ">Foragers in the Foodway (21+)</a>"
    )
    events, _ = parse_page(mutated, link_map)
    assert len(events) == 9
    assert "2182028" not in {e.external_id for e in events}


# --- Pagination terminator --------------------------------------------------------


def test_empty_page_yields_zero_cards(link_map):
    # Past-the-end pages are HTTP 200 with 0 Event cards (not 404); fetch()
    # terminates on card_count == 0.
    empty_page = (
        "<html><body><div id='catpage_events_list'></div>"
        "<p class='parks_pages cleardiv'><strong>Pages:</strong></p></body></html>"
    )
    events, n_cards = parse_page(empty_page, link_map)
    assert events == []
    assert n_cards == 0


# --- Invariants ---------------------------------------------------------------------


def test_external_ids_unique_and_numeric(events):
    ids = [e.external_id for e in events]
    assert all(i and i.isdigit() for i in ids)
    assert len(set(ids)) == len(ids)


def test_ids_unique(events):
    assert len({e.id for e in events}) == len(events)


def test_start_dt_is_tz_aware(events):
    assert events
    assert all(e.start_dt.tzinfo is not None for e in events)


def test_not_low_confidence(events):
    # Real description + URL on every row — the whole point vs. tvpp-9vvx.
    assert all(e.description and e.url for e in events)


def test_events_survive_db_upsert(events, tmp_path):
    from nyc_events import db

    db_path = str(tmp_path / "events.db")
    db.init_events(db_path)
    with db.connect_events(db_path) as conn:
        ins, _ = db.upsert_events(conn, events)
        assert ins == len(events)
        stored = db.search(conn, limit=100)
    assert len(stored) == len(events)
