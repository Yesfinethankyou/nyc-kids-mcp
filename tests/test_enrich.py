"""Enrichment pass (enrich.py).

Exercises the resolution ladder with injected geocoders so the suite never
touches the network: static tiers must not call out, network tiers must call
exactly once and then serve from geocode_cache, and forward geocoding must
backfill lat/lng. "36047034100" is a real tract GEOID in the shipped crosswalk
(-> "Crown Heights (North)").
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nyc_events import db, enrich
from nyc_events.models import Borough, Event, Price, compute_id

CROWN_HEIGHTS_TRACT = "36047034100"
CROWN_HEIGHTS_NTA = "Crown Heights (North)"


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_events(path)
    with db.connect_events(path) as c:
        yield c


class _Spy:
    """Counts calls and returns a canned result."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1
        return self.result


def _resolve(conn, **kw):
    defaults = dict(
        source="x", venue=None, borough=None, lat=None, lng=None,
        forward=_Spy(None), reverse=_Spy(None),
    )
    defaults.update(kw)
    return enrich.resolve(
        conn, defaults["source"], defaults["venue"], defaults["borough"],
        defaults["lat"], defaults["lng"],
        forward=defaults["forward"], reverse=defaults["reverse"],
    )


# --- static tiers: no network -------------------------------------------


def test_static_tier_does_not_call_geocoders(conn):
    fwd, rev = _Spy(None), _Spy(None)
    nb, lat, lng = _resolve(conn, source="domino_park", forward=fwd, reverse=rev)
    assert nb == "Williamsburg"
    assert fwd.calls == 0 and rev.calls == 0


def test_park_tier_resolves_without_network(conn):
    fwd, rev = _Spy(None), _Spy(None)
    nb, _, _ = _resolve(
        conn, source="nyc_permitted_events", venue="Prospect Park", forward=fwd, reverse=rev
    )
    assert nb == "Prospect Park"
    assert fwd.calls == 0 and rev.calls == 0


# --- tier 4: reverse-geocode existing coords ----------------------------


def test_reverse_tier_geocodes_then_caches(conn):
    rev = _Spy(CROWN_HEIGHTS_TRACT)
    nb, lat, lng = _resolve(conn, source="mommy_poppins", lat=40.674, lng=-73.944, reverse=rev)
    assert nb == CROWN_HEIGHTS_NTA
    assert (lat, lng) == (40.674, -73.944)  # coords unchanged
    assert rev.calls == 1
    # Second identical lookup is served from the cache.
    nb2, _, _ = _resolve(conn, source="mommy_poppins", lat=40.674, lng=-73.944, reverse=rev)
    assert nb2 == CROWN_HEIGHTS_NTA
    assert rev.calls == 1


# --- tier 5: forward-geocode the venue ----------------------------------


def test_forward_tier_backfills_coords_and_caches(conn):
    fwd = _Spy((40.674, -73.944, CROWN_HEIGHTS_TRACT))
    nb, lat, lng = _resolve(
        conn, source="mommy_poppins", venue="145 Brooklyn Ave", borough="Brooklyn", forward=fwd
    )
    assert nb == CROWN_HEIGHTS_NTA
    assert (lat, lng) == (40.674, -73.944)  # backfilled from the geocode
    assert fwd.calls == 1
    nb2, lat2, lng2 = _resolve(
        conn, source="mommy_poppins", venue="145 Brooklyn Ave", borough="Brooklyn", forward=fwd
    )
    assert (nb2, lat2, lng2) == (CROWN_HEIGHTS_NTA, 40.674, -73.944)
    assert fwd.calls == 1  # cache hit


def test_forward_miss_is_cached_negative(conn):
    fwd = _Spy(None)
    args = dict(source="mommy_poppins", venue="Nowhere Real", borough="Queens", forward=fwd)
    nb, lat, lng = _resolve(conn, **args)
    assert nb is None and lat is None and lng is None
    _resolve(conn, **args)
    assert fwd.calls == 1  # negative result remembered, not re-queried


# --- run() over a real table --------------------------------------------


def _ev(**kw):
    base = dict(
        source="domino_park", external_id=None, title="Kids Day",
        start_dt=datetime(2026, 7, 1, 14, tzinfo=UTC), venue_name="Domino Park",
        borough=Borough.BROOKLYN, neighborhood=None, price=Price.UNKNOWN, tags=["family"],
    )
    base.update(kw)
    ext = base.get("external_id") or base["title"]
    base["id"] = compute_id(base["source"], external_id=str(ext))
    return Event(**base)


def test_run_codes_neighborhoods_and_reports_counts(conn, tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    with db.connect_events(path) as c:
        db.upsert_events(c, [
            _ev(external_id="a", source="domino_park", venue_name="Domino Park"),
            _ev(external_id="b", source="mommy_poppins", title="Address Event",
                venue_name="145 Brooklyn Ave", lat=None, lng=None),
        ])

    fwd = _Spy((40.674, -73.944, CROWN_HEIGHTS_TRACT))
    considered, coded = enrich.run(path, forward=fwd, reverse=_Spy(None))
    assert considered == 2 and coded == 2

    with db.connect_events(path) as c:
        by_id = {e.title: e for e in db.search(c, start_after=datetime(2026, 1, 1, tzinfo=UTC))}
    assert by_id["Kids Day"].neighborhood == "Williamsburg"  # static tier
    addr = by_id["Address Event"]
    assert addr.neighborhood == CROWN_HEIGHTS_NTA       # forward-geocoded
    assert (addr.lat, addr.lng) == (40.674, -73.944)    # lat/lng backfilled


def test_run_skips_already_coded_rows(conn, tmp_path):
    path = str(tmp_path / "test.db")
    with db.connect_events(path) as c:
        db.upsert_events(c, [_ev(external_id="a", neighborhood="Preset")])
    considered, coded = enrich.run(path, forward=_Spy(None), reverse=_Spy(None))
    assert considered == 0 and coded == 0  # neighborhood already set, not reconsidered


# --- recode_all: propagate static-table corrections ----------------------


def test_recode_all_reprocesses_coded_rows(conn, tmp_path):
    # A row coded before a static-table correction keeps its stale label on
    # normal runs; --recode-all re-resolves it (here via the tier-1 constant).
    path = str(tmp_path / "test.db")
    with db.connect_events(path) as c:
        db.upsert_events(c, [_ev(external_id="a", neighborhood="Stale Label")])
    considered, coded = enrich.run(path, forward=_Spy(None), reverse=_Spy(None))
    assert (considered, coded) == (0, 0)  # normal run leaves it alone
    considered, coded = enrich.run(
        path, forward=_Spy(None), reverse=_Spy(None), recode_all=True
    )
    assert (considered, coded) == (1, 1)
    with db.connect_events(path) as c:
        ev = db.search(c, start_after=datetime(2026, 1, 1, tzinfo=UTC))[0]
    assert ev.neighborhood == "Williamsburg"  # domino_park tier-1 constant


def test_recode_all_keeps_label_when_resolution_fails(conn, tmp_path):
    # Recode only ever adds/updates coverage: a row whose resolution now
    # fails (no static hit, geocoder miss) keeps its existing label.
    path = str(tmp_path / "test.db")
    with db.connect_events(path) as c:
        db.upsert_events(c, [_ev(
            external_id="a", source="mommy_poppins", venue_name="Nowhere Real",
            neighborhood="Hand Coded",
        )])
    considered, coded = enrich.run(
        path, forward=_Spy(None), reverse=_Spy(None), recode_all=True
    )
    assert (considered, coded) == (1, 0)
    with db.connect_events(path) as c:
        ev = db.search(c, start_after=datetime(2026, 1, 1, tzinfo=UTC))[0]
    assert ev.neighborhood == "Hand Coded"
