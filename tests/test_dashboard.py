"""Tailnet dashboard tests (DASHBOARD-PLAN.md).

No network: temp DB seeded via init_events + upsert_events, exercised through
Starlette's TestClient. The db-layer numbers (source_health / catalog_stats /
connect_events_ro) are covered in test_db.py; this file covers the HTTP
surface: rendering, filter threading, validation, the XSS guard on scraped
fields, the GET-only contract, and the missing-DB friendly page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from nyc_events import config, dashboard, db
from nyc_events.models import Borough, Event, Price, compute_id
from nyc_events.sources import ENABLED_SOURCES

XSS = "<script>alert(1)</script>"


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
            ],
        )
    monkeypatch.setattr(config, "DB_PATH", path)
    return TestClient(dashboard.build_app())


# --- health page -----------------------------------------------------------


def test_health_page_renders_every_enabled_source(client):
    resp = client.get("/")
    assert resp.status_code == 200
    for cls in ENABLED_SOURCES:
        assert cls.name in resp.text


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


def test_limit_is_clamped_to_max():
    kwargs = dashboard._parse_browse_params({"limit": "9999"})
    assert kwargs["limit"] == dashboard._MAX_LIMIT
    kwargs = dashboard._parse_browse_params({"limit": "-5"})
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
