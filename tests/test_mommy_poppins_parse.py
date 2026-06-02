"""Mommy Poppins parser tests.

Uses captured fixtures (tests/fixtures/mommy_poppins_*.{html,xml}) plus
hand-crafted inputs for edge cases. Tests exercise pure helper functions
directly — no HTTP layer, no mocking httpx.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nyc_events.models import Borough, Price
from nyc_events.sources.mommy_poppins import (
    _extract_age_range,
    _extract_drupal_settings,
    _extract_jsonld,
    _extract_price,
    _infer_borough,
    _infer_tags,
    _parse_detail_page,
    _parse_sitemap_index,
    _parse_sitemap_page,
)

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL_HTML = (FIXTURES / "mommy_poppins_detail.html").read_text()
NO_DATE_HTML = (FIXTURES / "mommy_poppins_detail_no_date.html").read_text()
SITEMAP_XML = (FIXTURES / "mommy_poppins_sitemap_page.xml").read_text()

DETAIL_URL = "https://mommypoppins.com/new-york-city-kids/event/free-family-fun-day-prospect-park"
NO_DATE_URL = "https://mommypoppins.com/new-york-city-kids/event/summer-reading-party"


# --- Sitemap parsing --------------------------------------------------------


class TestSitemapIndex:
    def test_parse_sitemapindex_format(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://mommypoppins.com/sitemap1.xml</loc></sitemap>
          <sitemap><loc>https://mommypoppins.com/sitemap2.xml</loc></sitemap>
        </sitemapindex>"""
        urls = _parse_sitemap_index(xml)
        assert urls == [
            "https://mommypoppins.com/sitemap1.xml",
            "https://mommypoppins.com/sitemap2.xml",
        ]

    def test_parse_urlset_fallback(self):
        """If the index is actually a urlset (single sitemap), return those URLs."""
        urls = _parse_sitemap_index(SITEMAP_XML)
        assert len(urls) >= 1
        assert all(u.startswith("https://") for u in urls)


class TestSitemapPage:
    def test_filters_nyc_event_urls_only(self):
        urls = _parse_sitemap_page(SITEMAP_XML)
        # Should include NYC event URLs, exclude LA events and non-event pages
        assert DETAIL_URL in urls
        assert NO_DATE_URL in urls
        la_url = "https://mommypoppins.com/los-angeles-kids/event/la-family-festival"
        assert la_url not in urls
        todo_url = "https://mommypoppins.com/new-york-city-kids/things-to-do/best-playgrounds"
        assert todo_url not in urls

    def test_filters_by_lastmod_recency(self):
        # min_lastmod = 2026-03-01 should exclude the 2025-01-15 old event
        min_lastmod = datetime(2026, 3, 1, tzinfo=UTC)
        urls = _parse_sitemap_page(SITEMAP_XML, min_lastmod=min_lastmod)
        assert DETAIL_URL in urls
        assert "old-event-from-last-year" not in " ".join(urls)

    def test_all_urls_when_no_min_lastmod(self):
        urls = _parse_sitemap_page(SITEMAP_XML, min_lastmod=None)
        # Should include the old event too
        assert any("old-event-from-last-year" in u for u in urls)

    def test_custom_prefix(self):
        urls = _parse_sitemap_page(
            SITEMAP_XML,
            url_prefix="https://mommypoppins.com/los-angeles-kids/event/",
        )
        assert len(urls) == 1
        assert "la-family-festival" in urls[0]


# --- JSON-LD extraction -----------------------------------------------------


class TestJsonLd:
    def test_extracts_event_from_detail_page(self):
        jsonld = _extract_jsonld(DETAIL_HTML)
        assert jsonld is not None
        assert jsonld["@type"] == "Event"
        assert jsonld["name"] == "Free Family Fun Day at Prospect Park"
        assert "startDate" in jsonld

    def test_returns_none_for_no_jsonld(self):
        html = "<html><head></head><body><p>No JSON-LD here</p></body></html>"
        assert _extract_jsonld(html) is None

    def test_returns_none_for_non_event_jsonld(self):
        html = """<html><head>
        <script type="application/ld+json">{"@type": "WebPage", "name": "test"}</script>
        </head><body></body></html>"""
        assert _extract_jsonld(html) is None

    def test_handles_malformed_json(self):
        html = """<html><head>
        <script type="application/ld+json">{this is not valid json}</script>
        </head><body></body></html>"""
        assert _extract_jsonld(html) is None

    def test_handles_graph_wrapper(self):
        html = """<html><head>
        <script type="application/ld+json">
        [{"@type": "WebPage"}, {"@type": "Event", "name": "Graph Event"}]
        </script>
        </head><body></body></html>"""
        jsonld = _extract_jsonld(html)
        assert jsonld is not None
        assert jsonld["name"] == "Graph Event"


# --- Drupal settings extraction ----------------------------------------------


class TestDrupalSettings:
    def test_extracts_from_detail_page(self):
        drupal = _extract_drupal_settings(DETAIL_HTML)
        assert drupal is not None
        assert drupal["path"]["currentPath"] == "node/48231"
        markers = drupal["map_markers"]
        assert len(markers) == 1
        assert markers[0]["coords"]["latitude"] == 40.6602
        assert markers[0]["content"]["nid"] == "48231"

    def test_returns_none_when_absent(self):
        html = "<html><head></head><body>no scripts</body></html>"
        assert _extract_drupal_settings(html) is None


# --- Age range extraction ----------------------------------------------------


class TestAgeRange:
    def test_range_format(self):
        assert _extract_age_range("Age: 2-12") == (2, 12)
        assert _extract_age_range("Ages: 5 - 10") == (5, 10)
        assert _extract_age_range("for ages 3 to 8") == (3, 8)

    def test_all_ages(self):
        assert _extract_age_range("Fun for all ages!") == (0, 99)

    def test_plus_format(self):
        assert _extract_age_range("Ages: 5+") == (5, None)
        assert _extract_age_range("Age: 3+") == (3, None)

    def test_no_age_info(self):
        assert _extract_age_range("No age information here") == (None, None)

    def test_extracts_from_fixture_body(self):
        age_min, age_max = _extract_age_range(DETAIL_HTML)
        assert age_min == 2
        assert age_max == 12


# --- Price extraction --------------------------------------------------------


class TestPrice:
    def test_free_from_jsonld_price_zero(self):
        jsonld = {"offers": {"@type": "Offer", "price": "0"}}
        assert _extract_price(jsonld, "") == Price.FREE

    def test_paid_from_jsonld_price_nonzero(self):
        jsonld = {"offers": {"@type": "Offer", "price": "25.00"}}
        assert _extract_price(jsonld, "") == Price.PAID

    def test_free_with_admission_is_paid(self):
        assert _extract_price(None, "Free with admission to the museum") == Price.PAID
        assert _extract_price(None, "free with museum admission required") == Price.PAID

    def test_free_from_body_text(self):
        assert _extract_price(None, "This event is free and open to the public") == Price.FREE

    def test_paid_from_dollar_sign(self):
        assert _extract_price(None, "Tickets are $15 per person") == Price.PAID

    def test_unknown_when_no_signal(self):
        assert _extract_price(None, "Join us for a great event") == Price.UNKNOWN

    def test_jsonld_offers_list(self):
        jsonld = {"offers": [{"@type": "Offer", "price": "10"}]}
        assert _extract_price(jsonld, "") == Price.PAID

    def test_free_from_fixture(self):
        jsonld = _extract_jsonld(DETAIL_HTML)
        price = _extract_price(jsonld, "")
        assert price == Price.FREE


# --- Borough inference -------------------------------------------------------


class TestBoroughInference:
    def test_from_coordinates_brooklyn(self):
        assert _infer_borough(40.6602, -73.9690, None) == Borough.BROOKLYN

    def test_from_coordinates_manhattan(self):
        # Central Park
        assert _infer_borough(40.7829, -73.9654, None) == Borough.MANHATTAN

    def test_from_coordinates_queens(self):
        # Flushing Meadows
        assert _infer_borough(40.7400, -73.8407, None) == Borough.QUEENS

    def test_from_coordinates_bronx(self):
        # Bronx Zoo
        assert _infer_borough(40.8506, -73.8769, None) == Borough.BRONX

    def test_from_coordinates_staten_island(self):
        assert _infer_borough(40.5795, -74.1502, None) == Borough.STATEN_ISLAND

    def test_venue_lookup_when_no_coords(self):
        assert _infer_borough(None, None, "Prospect Park Bandshell") == Borough.BROOKLYN
        assert _infer_borough(None, None, "Central Park Zoo") == Borough.MANHATTAN
        assert _infer_borough(None, None, "Bronx Zoo Main Entrance") == Borough.BRONX

    def test_unknown_when_no_signal(self):
        assert _infer_borough(None, None, None) is None
        assert _infer_borough(None, None, "Some Random Venue") is None

    def test_coords_take_priority_over_venue(self):
        # Coords say Brooklyn, venue says Manhattan — coords win
        assert _infer_borough(40.6602, -73.9690, "Central Park") == Borough.BROOKLYN


# --- Tag inference -----------------------------------------------------------


class TestTagInference:
    def test_multiple_tags(self):
        tags = _infer_tags(
            "Free Family Fun Day at Prospect Park",
            "crafts, music, nature walks",
        )
        assert "family" in tags
        assert "free" in tags
        assert "nature" in tags
        assert "arts & crafts" in tags
        assert "music" in tags

    def test_from_title_only(self):
        tags = _infer_tags("Kids Science Workshop", None)
        assert "educational" in tags
        assert "best for kids" in tags

    def test_empty_for_generic(self):
        tags = _infer_tags("Something Happened", None)
        assert tags == []


# --- Detail page parsing (end-to-end) ----------------------------------------


class TestDetailPageParsing:
    def test_happy_path(self):
        ev = _parse_detail_page(DETAIL_HTML, DETAIL_URL)
        assert ev is not None
        assert ev.title == "Free Family Fun Day at Prospect Park"
        assert ev.source == "mommy_poppins"
        assert ev.start_dt.year == 2026
        assert ev.start_dt.month == 6
        assert ev.start_dt.day == 15
        assert ev.end_dt is not None
        assert ev.venue_name == "Prospect Park Bandshell"
        assert ev.borough == Borough.BROOKLYN
        assert ev.lat == pytest.approx(40.6602, abs=0.01)
        assert ev.lng == pytest.approx(-73.9690, abs=0.01)
        assert ev.price == Price.FREE
        assert ev.age_min == 2
        assert ev.age_max == 12
        assert ev.url == "https://mommypoppins.com/new-york-city-kids/event/free-family-fun-day-prospect-park"
        assert ev.description is not None
        assert "face painting" in ev.description
        assert len(ev.tags) > 0
        assert ev.raw_payload is not None

    def test_missing_start_date_returns_none(self):
        ev = _parse_detail_page(NO_DATE_HTML, NO_DATE_URL)
        assert ev is None

    def test_external_id_is_nid(self):
        ev = _parse_detail_page(DETAIL_HTML, DETAIL_URL)
        assert ev is not None
        assert ev.external_id == "48231"

    def test_external_id_falls_back_to_slug(self):
        # Remove drupal settings and googletag so nid is unavailable
        html = DETAIL_HTML.replace("currentPath", "xPath")
        html = html.replace("setTargeting", "xTarget")
        html = html.replace('"nid"', '"xid"')
        ev = _parse_detail_page(html, DETAIL_URL)
        assert ev is not None
        assert ev.external_id == "free-family-fun-day-prospect-park"

    def test_stable_id_is_deterministic(self):
        a = _parse_detail_page(DETAIL_HTML, DETAIL_URL)
        b = _parse_detail_page(DETAIL_HTML, DETAIL_URL)
        assert a is not None and b is not None
        assert a.id == b.id
        assert len(a.id) == 16

    def test_raw_payload_set(self):
        ev = _parse_detail_page(DETAIL_HTML, DETAIL_URL)
        assert ev is not None
        raw = json.loads(ev.raw_payload)
        assert "jsonld" in raw
        assert "map_markers" in raw
        assert "page_url" in raw

    def test_no_title_returns_none(self):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Event", "startDate": "2026-06-15T10:00:00-04:00"}
        </script>
        </head><body></body></html>"""
        assert _parse_detail_page(html, "https://example.com/event/test") is None

    def test_malformed_jsonld_skips_gracefully(self):
        html = """<html><head>
        <script type="application/ld+json">{not valid json at all!</script>
        <script>{"path":{"currentPath":"node/99999"}}</script>
        </head><body></body></html>"""
        # No JSON-LD + no startDate in drupalSettings → None
        ev = _parse_detail_page(html, "https://example.com/event/test")
        assert ev is None

    def test_url_falls_back_to_page_url(self):
        # JSON-LD with no url field
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Event", "name": "Test Event", "startDate": "2026-07-01T10:00:00-04:00"}
        </script>
        </head><body></body></html>"""
        page_url = "https://mommypoppins.com/new-york-city-kids/event/test-event"
        ev = _parse_detail_page(html, page_url)
        assert ev is not None
        assert ev.url == page_url

    def test_venue_from_drupal_markers_when_no_jsonld_location(self):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Event", "name": "Marker Event",
         "startDate": "2026-07-01T10:00:00-04:00"}
        </script>
        <script>{"path":{"currentPath":"node/77777"},
        "map_markers":[{"coords":{"latitude":40.7829,
        "longitude":-73.9654},"title":"m0",
        "content":{"nid":"77777","title":"Great Lawn",
        "node_title":"Marker Event"}}]}</script>
        </head><body></body></html>"""
        ev = _parse_detail_page(html, "https://example.com/event/test")
        assert ev is not None
        assert ev.venue_name == "Great Lawn"
        assert ev.lat == pytest.approx(40.7829, abs=0.01)
        assert ev.borough == Borough.MANHATTAN
