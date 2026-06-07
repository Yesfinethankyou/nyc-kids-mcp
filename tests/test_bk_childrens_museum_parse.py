"""Parser tests for the Brooklyn Children's Museum source.

Uses the captured fixture (tests/fixtures/bk_childrens_museum_sample.html)
and inline HTML snippets. Does not make network calls.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, date

from selectolax.parser import HTMLParser

from nyc_events.models import Borough, Price
from nyc_events.sources.bk_childrens_museum import (
    _date_from_header,
    _date_from_slug,
    _infer_tags,
    _parse_article,
    _parse_listing_page,
    _parse_time_str,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "bk_childrens_museum_sample.html"
TODAY = date(2026, 6, 6)


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


def test_date_from_slug_with_date():
    assert _date_from_slug("play-session-2-2026-06-06") == date(2026, 6, 6)
    assert _date_from_slug("play-at-bcm-2026-06-10") == date(2026, 6, 10)


def test_date_from_slug_no_date():
    assert _date_from_slug("member-monday") is None
    assert _date_from_slug("bcm-closed") is None


def test_date_from_header_current_year():
    result = _date_from_header("Saturday, June 6", date(2026, 6, 1))
    assert result == date(2026, 6, 6)


def test_date_from_header_rolls_to_next_year():
    # If June 6 is in the past relative to today, use next year.
    result = _date_from_header("Saturday, June 6", date(2026, 6, 10))
    assert result == date(2027, 6, 6)


def test_date_from_header_invalid_month():
    assert _date_from_header("Saturday, Julember 6", date(2026, 6, 1)) is None


def test_parse_time_str_start_and_end():
    start, end = _parse_time_str("10:00 am – 5:00 pm")
    assert start == (10, 0)
    assert end == (17, 0)


def test_parse_time_str_start_only():
    start, end = _parse_time_str("11:00 am")
    assert start == (11, 0)
    assert end is None


def test_parse_time_str_none():
    assert _parse_time_str(None) == (None, None)


def test_parse_time_str_pm_noon():
    start, end = _parse_time_str("12:00 pm – 2:00 pm")
    assert start == (12, 0)
    assert end == (14, 0)


def test_infer_tags_family_default():
    tags = _infer_tags("Play at BCM", "Come explore exhibits.")
    assert "family" in tags
    assert "best for kids" in tags


def test_infer_tags_arts():
    tags = _infer_tags("ColorLab Workshop", "Craft and painting activities.")
    assert "arts & crafts" in tags


# ---------------------------------------------------------------------------
# Fixture-based integration tests
# ---------------------------------------------------------------------------


def _load_articles(html: str):
    tree = HTMLParser(html)
    return list(tree.css("article.tease-event"))


def test_fixture_parses_expected_count():
    html = FIXTURE.read_text()
    events = _parse_listing_page(html, TODAY)
    # Fixture has 4 articles; 3 with date in slug, 1 (Member Monday) without.
    assert len(events) == 4


def test_happy_path_slug_date():
    html = FIXTURE.read_text()
    articles = _load_articles(html)
    ev = _parse_article(articles[0], TODAY)

    assert ev is not None
    assert ev.title == "Play at BCM"
    assert ev.url == "https://www.brooklynkids.org/events/play-session-2-2026-06-06/"
    assert ev.start_dt.date() == date(2026, 6, 6)
    assert ev.start_dt.hour == 14  # 10:00 am EDT = 14:00 UTC
    assert ev.end_dt is not None
    assert ev.end_dt.date() == date(2026, 6, 6)
    assert ev.venue_name == "Brooklyn Children's Museum"
    assert ev.borough == Borough.BROOKLYN
    assert ev.price == Price.PAID
    assert ev.source == "bk_childrens_museum"
    assert ev.external_id is not None
    assert "family" in ev.tags


def test_second_event_different_date():
    html = FIXTURE.read_text()
    articles = _load_articles(html)
    ev = _parse_article(articles[1], TODAY)

    assert ev is not None
    assert ev.start_dt.date() == date(2026, 6, 7)


def test_member_monday_no_slug_date():
    """Member Monday slug has no date — must fall back to header parsing."""
    html = FIXTURE.read_text()
    articles = _load_articles(html)
    # Member Monday is the 4th article (index 3)
    ev = _parse_article(articles[3], TODAY)

    assert ev is not None
    assert ev.title == "Member Monday"
    assert ev.start_dt.date() == date(2026, 6, 15)
    assert ev.url == "https://www.brooklynkids.org/events/member-monday/"
    assert ev.external_id == "5900"


def test_member_monday_is_free():
    """Member Monday description mentions 'free' → Price.FREE."""
    html = FIXTURE.read_text()
    articles = _load_articles(html)
    ev = _parse_article(articles[3], TODAY)
    assert ev is not None
    assert ev.price == Price.FREE


def test_closure_notice_is_skipped():
    """Events with 'Closed' in the title should be filtered out."""
    closure_html = """
    <article class="tease tease-event" id="tease-9999">
        <div class="font-black mb-4 mt-11">Sunday, June 8</div>
        <div class="bcm-flex-row">
          <div class="bcm-flex-col w-2/3">
            <h2 class="h2">
              <a href="https://www.brooklynkids.org/events/bcm-closed/">BCM Closed</a>
            </h2>
            <div class="font-black mb-3">10:00 am – 5:00 pm</div>
            <div>Brooklyn Children's Museum is closed.</div>
          </div>
        </div>
    </article>
    """
    tree = HTMLParser(closure_html)
    article = tree.css_first("article.tease-event")
    ev = _parse_article(article, TODAY)
    assert ev is None


def test_article_missing_title_returns_none():
    bad_html = """
    <article class="tease tease-event" id="tease-1">
        <div class="font-black mb-4 mt-11">Saturday, June 6</div>
        <div class="bcm-flex-row">
          <div class="bcm-flex-col w-2/3">
          </div>
        </div>
    </article>
    """
    tree = HTMLParser(bad_html)
    article = tree.css_first("article.tease-event")
    assert _parse_article(article, TODAY) is None


def test_article_missing_date_returns_none():
    bad_html = """
    <article class="tease tease-event" id="tease-2">
        <div class="bcm-flex-row">
          <div class="bcm-flex-col w-2/3">
            <h2 class="h2">
              <a href="https://www.brooklynkids.org/events/some-event/">Some Event</a>
            </h2>
          </div>
        </div>
    </article>
    """
    tree = HTMLParser(bad_html)
    article = tree.css_first("article.tease-event")
    assert _parse_article(article, TODAY) is None


def test_event_ids_are_unique():
    html = FIXTURE.read_text()
    events = _parse_listing_page(html, TODAY)
    ids = [ev.id for ev in events]
    assert len(ids) == len(set(ids)), "Duplicate event IDs generated"


def test_all_events_are_brooklyn():
    html = FIXTURE.read_text()
    events = _parse_listing_page(html, TODAY)
    assert all(ev.borough == Borough.BROOKLYN for ev in events)


def test_start_dt_is_utc_aware():
    html = FIXTURE.read_text()
    events = _parse_listing_page(html, TODAY)
    for ev in events:
        assert ev.start_dt.tzinfo is not None
        assert ev.start_dt.tzinfo == UTC or ev.start_dt.utcoffset().total_seconds() == 0
