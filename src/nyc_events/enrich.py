"""Location-enrichment pass: code each event's neighborhood, backfill lat/lng.

Runs as a second nightly phase after ingest (see ingest.main). Sources stay
dumb — all location logic lives here. The resolution ladder is
deterministic-first, network-last:

    1-3  static_neighborhood()  (source constant / venue dict / park table)
    4    reverse-geocode existing lat/lng -> NTA
    5    forward-geocode "venue, borough, NY" -> lat/lng (backfilled) + NTA

Every network result (including negatives) is cached in geocode_cache, so a
given venue is geocoded at most once, ever. Only rows with neighborhood IS NULL
are processed; the upsert nulls neighborhood each ingest, so the pass refreshes
the whole table nightly but hits the network only for venues it has never seen.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable

import httpx

from . import db, geocode
from .sources._neighborhoods import normalize_name, nta_for_tract, static_neighborhood

logger = logging.getLogger(__name__)

# Injected for tests so the suite never touches the network.
ForwardFn = Callable[[str], "tuple[float, float, str] | None"]
ReverseFn = Callable[[float, float], "str | None"]

# Census matches NYC street addresses by their USPS city. Manhattan's is
# "New York", not "Manhattan"; the other boroughs use their own name.
_BOROUGH_CITY = {"Manhattan": "New York"}


def _geocode_query(venue: str, borough: str | None) -> str:
    if not borough:
        return f"{venue}, New York, NY"
    return f"{venue}, {_BOROUGH_CITY.get(borough, borough)}, NY"


def _round(v: float) -> float:
    return round(v, 5)


def resolve(
    conn: sqlite3.Connection,
    source: str,
    venue: str | None,
    borough: str | None,
    lat: float | None,
    lng: float | None,
    *,
    forward: ForwardFn,
    reverse: ReverseFn,
) -> tuple[str | None, float | None, float | None]:
    """Resolve (neighborhood, lat, lng) for one row. The static tiers never
    invoke the geocoders; lat/lng are returned so a forward-geocoded row can
    backfill its coordinates."""
    # Tiers 1-3: deterministic, no network.
    nb = static_neighborhood(source, venue, borough)
    if nb:
        return nb, lat, lng

    # Tier 4: the row already has coordinates -> reverse-geocode them.
    if lat is not None and lng is not None:
        key = f"rev:{_round(lat)},{_round(lng)}"
        cached = db.get_geocode(conn, key)
        if cached is not None:
            return cached[2], lat, lng
        geoid = reverse(lat, lng)
        nta = nta_for_tract(geoid)
        db.put_geocode(conn, key, lat, lng, nta)
        return nta, lat, lng

    # Tier 5: forward-geocode the venue string; backfills lat/lng on a hit.
    if venue:
        key = f"fwd:{normalize_name(venue)}|{normalize_name(borough)}"
        cached = db.get_geocode(conn, key)
        if cached is not None:
            return cached[2], cached[0] if cached[0] is not None else lat, (
                cached[1] if cached[1] is not None else lng
            )
        hit = forward(_geocode_query(venue, borough))
        if hit is None:
            db.put_geocode(conn, key, None, None, None)  # remember the miss
            return None, lat, lng
        glat, glng, geoid = hit
        nta = nta_for_tract(geoid)
        db.put_geocode(conn, key, glat, glng, nta)
        return nta, glat, glng

    return None, lat, lng


def run(
    db_path: str,
    *,
    forward: ForwardFn | None = None,
    reverse: ReverseFn | None = None,
) -> tuple[int, int]:
    """Enrich all rows with a null neighborhood. Returns (considered, coded).
    The geocoders default to the live Census client; tests inject fakes."""
    with httpx.Client(timeout=30.0) as client:
        fwd = forward or (lambda q: geocode.forward(q, client=client))
        rev = reverse or (lambda y, x: geocode.reverse(y, x, client=client))
        with db.connect_events(db_path) as conn:
            rows = conn.execute(
                "SELECT id, source, venue_name, borough, lat, lng "
                "FROM events WHERE neighborhood IS NULL"
            ).fetchall()
            coded = 0
            for r in rows:
                try:
                    nb, lat, lng = resolve(
                        conn, r["source"], r["venue_name"], r["borough"],
                        r["lat"], r["lng"], forward=fwd, reverse=rev,
                    )
                except Exception as exc:  # noqa: BLE001 — one bad row must not abort the pass
                    logger.warning("enrich: row %s failed: %r", r["id"], exc)
                    continue
                if nb:
                    conn.execute(
                        "UPDATE events SET neighborhood = ?, "
                        "lat = COALESCE(lat, ?), lng = COALESCE(lng, ?) WHERE id = ?",
                        (nb, lat, lng, r["id"]),
                    )
                    coded += 1
            conn.commit()
    return len(rows), coded


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db_path = os.environ.get("DB_PATH", "data/events.db")
    considered, coded = run(db_path)
    print(f"enrich: {coded}/{considered} rows coded with a neighborhood")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
