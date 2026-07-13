"""Tailnet dashboard tests.

No network: temp DB seeded via init_events + upsert_events, exercised through
Starlette's TestClient. The db-layer numbers (source_health / catalog_stats /
connect_events_ro) are covered in test_db.py; this file covers the HTTP
surface: rendering, filter threading, validation, the XSS guard on scraped
fields, the GET-only contract, and the missing-DB friendly page.
"""

from __future__ import annotations

import html
import re
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from starlette.datastructures import QueryParams
from starlette.testclient import TestClient

from nyc_events import config, dashboard, db
from nyc_events.models import Borough, Event, Price, compute_id
from nyc_events.sources import ENABLED_SOURCES

XSS = "<script>alert(1)</script>"

RANGED_START = (datetime.now(UTC) + timedelta(days=2)).replace(
    hour=16, minute=0, second=0, microsecond=0
)


def _ev(**overrides):
    base = dict(
        source="testsrc",
        external_id=None,
        title="Toddler Music in Prospect Park",
        description="Sing-along for ages 1-4.",
        url="https://example.com/e1",
        start_dt=datetime.now(UTC) + timedelta(days=2),
        end_dt=None,
        venue_name="Prospect Park",
        borough=Borough.BROOKLYN,
        neighborhood="Prospect Heights",
        price=Price.FREE,
        tags=["music"],
    )
    base.update(overrides)
    if "id" not in base:
        ext = base.get("external_id") or base["title"]
        base["id"] = compute_id(base["source"], external_id=str(ext))
    return Event(**base)


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "events.db")
    db.init_events(path)
    with db.connect_events(path) as conn:
        db.upsert_events(
            conn,
            [
                _ev(external_id="e1"),
                _ev(
                    external_id="e2",
                    title="Queens Science Fair",
                    borough=Borough.QUEENS,
                    neighborhood="Astoria",
                    price=Price.PAID,
                    tags=["science"],
                ),
                # XSS canary: every field here is scraped from the public web.
                _ev(
                    external_id="xss",
                    title=XSS,
                    description=f"desc {XSS}",
                    venue_name=f"venue {XSS}",
                ),
                # permit-style low-confidence row
                _ev(
                    external_id="permit",
                    title="Permit Row",
                    description=None,
                    url=None,
                ),
                # has an end time → browse renders a same-day range.
                # 16:00 UTC is 11:00/12:00 NYC, so +4h never crosses the
                # local-midnight boundary regardless of when the suite runs.
                _ev(
                    external_id="ranged",
                    title="Ranged Event",
                    start_dt=RANGED_START,
                    end_dt=RANGED_START + timedelta(hours=4),
                ),
                # scheme-smuggling canary: html.escape alone wouldn't stop
                # this executing on click if it were rendered as an anchor
                _ev(
                    external_id="evil-url",
                    title="Scheme Smuggler",
                    url="javascript:alert(1)",
                ),
            ],
        )
    monkeypatch.setattr(config, "DB_PATH", path)
    return TestClient(dashboard.build_app())


# --- health page -----------------------------------------------------------


def test_health_page_renders_every_enabled_source(client):
    resp = client.get("/")
    assert resp.status_code == 200
    for cls in ENABLED_SOURCES:
        # html.escape entity-encodes apostrophes (e.g. "Children's"), so
        # compare against the escaped label, not the raw display string.
        assert html.escape(dashboard._source_label(cls.name)) in resp.text


def test_health_page_shows_catalog_strip_and_db_source(client):
    resp = client.get("/")
    # testsrc has rows but isn't in ENABLED_SOURCES → shown, marked unregistered
    assert "testsrc" in resp.text
    assert "not registered" in resp.text
    assert "events</span>" in resp.text  # catalog strip rendered


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


# --- browse page -----------------------------------------------------------


def test_events_page_lists_seeded_events(client):
    resp = client.get("/events")
    assert resp.status_code == 200
    assert "Toddler Music in Prospect Park" in resp.text
    assert "Queens Science Fair" in resp.text


def test_events_filters_thread_through(client):
    resp = client.get("/events", params={"borough": "Queens"})
    assert "Queens Science Fair" in resp.text
    assert "Toddler Music in Prospect Park" not in resp.text

    resp = client.get("/events", params={"q": "science"})
    assert "Queens Science Fair" in resp.text
    assert "Toddler Music in Prospect Park" not in resp.text

    resp = client.get("/events", params={"free_only": "1"})
    assert "Toddler Music in Prospect Park" in resp.text
    assert "Queens Science Fair" not in resp.text

    resp = client.get("/events", params={"exclude_low_confidence": "1"})
    assert "Permit Row" not in resp.text


def test_events_date_window(client):
    future = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")
    resp = client.get("/events", params={"start_date": future})
    assert resp.status_code == 200
    assert "No events match" in resp.text


def test_events_bad_date_renders_400_not_traceback(client):
    resp = client.get("/events", params={"start_date": "junk"})
    assert resp.status_code == 400
    assert "must be YYYY-MM-DD" in resp.text
    assert "Traceback" not in resp.text


def test_events_end_before_start_renders_400(client):
    resp = client.get(
        "/events", params={"start_date": "2027-06-10", "end_date": "2027-06-01"}
    )
    assert resp.status_code == 400
    assert "before the window start" in resp.text


def test_events_bad_age_and_limit_render_400(client):
    assert client.get("/events", params={"age": "four"}).status_code == 400
    assert client.get("/events", params={"limit": "lots"}).status_code == 400


def test_multi_select_controls_render_with_facet_options(client):
    # Borough/neighborhood/source are native <select multiple> — no JS, no
    # more guessing NTA spellings blind, and no cap of one filter value.
    resp = client.get("/events")
    assert "<select name='borough' multiple" in resp.text
    assert "<select name='neighborhood' multiple" in resp.text
    assert "<select name='source' multiple" in resp.text
    assert "<option value='Astoria'>Astoria</option>" in resp.text
    assert "<option value='Queens'>Queens</option>" in resp.text
    assert "<option value='testsrc'>testsrc</option>" in resp.text


def test_multi_select_preselects_every_chosen_option(client):
    # Fixture boroughs are Brooklyn + Queens only; select just Queens so
    # Brooklyn's un-selected rendering is also pinned.
    resp = client.get("/events", params={"borough": ["Queens"], "source": "testsrc"})
    assert "<option value='Queens' selected>Queens</option>" in resp.text
    assert "<option value='Brooklyn'>Brooklyn</option>" in resp.text  # not selected
    assert "<option value='testsrc' selected>testsrc</option>" in resp.text


def test_multi_value_borough_filter_returns_union_not_intersection(client):
    resp = client.get("/events", params={"borough": ["Queens", "Brooklyn"]})
    assert "Queens Science Fair" in resp.text
    assert "Toddler Music in Prospect Park" in resp.text  # Brooklyn


def test_multi_value_neighborhood_filter_is_exact_match(client):
    resp = client.get("/events", params={"neighborhood": ["Astoria"]})
    assert "Queens Science Fair" in resp.text
    assert "Toddler Music in Prospect Park" not in resp.text


def test_source_label_maps_known_sources_and_falls_back():
    assert dashboard._source_label("bpl") == "Brooklyn Public Library"
    assert dashboard._source_label("nycgovparks_events") == "NYC Parks"
    assert dashboard._source_label("some_future_source") == "some_future_source"
    assert dashboard._source_label(None) == ""


def test_neighborhood_search_narrows_dropdown_options(client):
    # No JS on this dashboard (CSP has no script-src) — the search box is a
    # plain form field that re-renders the option list server-side.
    resp = client.get("/events", params={"nbhd_q": "ast"})
    assert "<option value='Astoria'>Astoria</option>" in resp.text
    assert "<option value='Prospect Heights'>Prospect Heights</option>" not in resp.text


def test_neighborhood_search_keeps_existing_selection_in_the_list(client):
    # Selecting "Prospect Heights" then searching for text that doesn't match
    # it must not silently drop the selection out of the option list.
    resp = client.get(
        "/events", params={"neighborhood": "Prospect Heights", "nbhd_q": "ast"}
    )
    assert "<option value='Prospect Heights' selected>Prospect Heights</option>" in resp.text
    assert "<option value='Astoria'>Astoria</option>" in resp.text


def test_neighborhood_search_text_carried_into_preset_links(client):
    resp = client.get("/events", params={"nbhd_q": "ast"})
    presets = re.search(r"<p class='presets'>(.*?)</p>", resp.text).group(1)
    assert presets.count("nbhd_q=ast") == 3


def test_preset_links_preserve_active_filters(client):
    resp = client.get("/events", params={"borough": "Queens", "free_only": "1"})
    for label in ("today", "this weekend", "next 7 days"):
        assert f"{label}</a>" in resp.text
    # Non-date filters ride along in the preset hrefs (form renders them as
    # <select>/checkbox state, so a query-string form only occurs in presets).
    assert "borough=Queens" in resp.text
    assert "free_only=1" in resp.text
    assert resp.text.count("start_date=") == 3


def test_preset_links_carry_every_multi_select_value(client):
    # A plain params.get() would silently drop every selection past the
    # first — pin that both ride along into the preset hrefs, not just one.
    resp = client.get("/events", params={"borough": ["Queens", "Brooklyn"]})
    presets = re.search(r"<p class='presets'>(.*?)</p>", resp.text).group(1)
    assert presets.count("borough=Queens") == 3  # today / weekend / next-7-days
    assert presets.count("borough=Brooklyn") == 3


def test_result_count_flags_reaching_the_limit(client):
    resp = client.get("/events", params={"limit": "1"})
    assert "limit reached" in resp.text
    resp = client.get("/events")  # 5 seeded rows, default limit 50
    assert "limit reached" not in resp.text


def test_title_tooltip_carries_truncated_description(client):
    resp = client.get("/events")
    assert "title='Sing-along for ages 1-4.'" in resp.text


def test_reset_link_present(client):
    resp = client.get("/events")
    assert "<a href='/events'>reset</a>" in resp.text


def test_source_column_rendered(client):
    resp = client.get("/events")
    assert "<th>Source</th>" in resp.text
    assert "<td>testsrc</td>" in resp.text


def test_when_column_shows_same_day_time_range(client):
    start = RANGED_START.astimezone(dashboard.NYC_TZ)
    end = (RANGED_START + timedelta(hours=4)).astimezone(dashboard.NYC_TZ)
    expected = f"{start.strftime('%Y-%m-%d %H:%M')}–{end.strftime('%H:%M')}"
    resp = client.get("/events")
    assert expected in resp.text
    # An event without an end time still renders as a bare start stamp.
    assert "Toddler Music in Prospect Park" in resp.text


def test_limit_is_clamped_to_max():
    # _parse_browse_params is always called with request.query_params (a
    # Starlette QueryParams, which getlist() needs) — build one directly
    # rather than a plain dict.
    kwargs = dashboard._parse_browse_params(QueryParams({"limit": "9999"}))
    assert kwargs["limit"] == dashboard._MAX_LIMIT
    kwargs = dashboard._parse_browse_params(QueryParams({"limit": "-5"}))
    assert kwargs["limit"] == 1


# --- detail page -----------------------------------------------------------


def test_event_detail_roundtrip(client):
    event_id = compute_id("testsrc", external_id="e2")
    listing = client.get("/events")
    assert f"/event/{event_id}" in listing.text
    resp = client.get(f"/event/{event_id}")
    assert resp.status_code == 200
    assert "Queens Science Fair" in resp.text
    assert "Astoria" in resp.text


def test_event_detail_unknown_id_404(client):
    resp = client.get("/event/doesnotexist")
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


# --- XSS guard ---------------------------------------------------------------


def test_non_http_url_schemes_are_never_rendered_as_links(client):
    listing = client.get("/events")
    assert "href='javascript:" not in listing.text
    assert "Scheme Smuggler" in listing.text  # row still renders, url as text

    event_id = compute_id("testsrc", external_id="evil-url")
    detail = client.get(f"/event/{event_id}")
    assert "href='javascript:" not in detail.text
    assert "javascript:alert(1)" in detail.text  # visible as text, unlinked


def test_security_headers_on_every_page(client):
    event_id = compute_id("testsrc", external_id="e2")
    for url in ("/", "/events", f"/event/{event_id}", "/events?start_date=junk"):
        resp = client.get(url)
        assert resp.headers["X-Frame-Options"] == "DENY", url
        assert resp.headers["X-Content-Type-Options"] == "nosniff", url
        assert resp.headers["Referrer-Policy"] == "no-referrer", url
        assert "default-src 'none'" in resp.headers["Content-Security-Policy"], url


def test_scraped_fields_are_escaped_on_listing_and_detail(client):
    listing = client.get("/events")
    assert XSS not in listing.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in listing.text

    event_id = compute_id("testsrc", external_id="xss")
    detail = client.get(f"/event/{event_id}")
    assert XSS not in detail.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail.text


# --- read-only contract ------------------------------------------------------


def test_all_routes_are_get_only():
    for route in dashboard.build_app().routes:
        # HEAD is implied by GET in Starlette; nothing else is allowed.
        assert set(route.methods) <= {"GET", "HEAD"}, route.path


def test_post_is_rejected(client):
    assert client.post("/events").status_code == 405
    assert client.post("/").status_code == 405


# --- missing-DB path ----------------------------------------------------------


def test_missing_db_renders_friendly_page(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "nope.db"))
    client = TestClient(dashboard.build_app())
    for url in ("/", "/events", "/event/abc"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert "No database yet" in resp.text


def test_unrelated_operational_error_is_not_mislabeled_as_missing_db(client, monkeypatch):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "source_health", boom)
    with pytest.raises(sqlite3.OperationalError):
        client.get("/")
