"""Build src/nyc_events/data/park_neighborhoods.json.

A one-shot data-prep script (not run at ingest time). It maps NYC park names to
their neighborhood (NTA) so the enrichment pass can code the permit source's
park-keyed rows without geocoding each one live.

Pipeline (all NYC open data, no API keys):
  1. Pull NYC Parks "Parks Properties" (Socrata enfh-gkve): name + street
     address + borough + zip.
  2. Batch-geocode the addresses with the US Census geocoder -> 2020 census
     tract GEOID.
  3. Map tract GEOID -> NTA name via the tract_to_nta.json crosswalk
     (build_tract_nta.py must run first).
  4. Emit {normalized_park_name: nta_name}, keyed by both signname and name311
     so permit `event_location` strings match either.

Run (after build_tract_nta.py):
    .venv/bin/python scripts/build_park_neighborhoods.py
Provenance: https://data.cityofnewyork.us/d/enfh-gkve
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PARKS_URL = "https://data.cityofnewyork.us/resource/enfh-gkve.json"
BATCH_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
COORD_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
OUT = ROOT / "src" / "nyc_events" / "data" / "park_neighborhoods.json"

# Parks Properties borough code -> Census "city" component. NYC street matches
# lean on the ZIP, so an imperfect city (Queens USPS cities are neighborhood
# names) still resolves.
BORO_CITY = {"M": "New York", "B": "Brooklyn", "Q": "Queens", "X": "Bronx", "R": "Staten Island"}


def fetch_parks() -> list[dict]:
    with httpx.Client(timeout=120.0) as client:
        resp = client.get(
            PARKS_URL,
            params={
                "$select": "signname,name311,borough,address,zipcode,retired,multipolygon",
                "$limit": 50000,
            },
        )
        resp.raise_for_status()
        return resp.json()


def first_zip(p: dict) -> str:
    # Big parks list several ZIPs ("11364, 11423, 11427"); the batch CSV needs
    # one. The Census matcher leans on the street range, so the first is fine.
    return (p.get("zipcode") or "").split(",")[0].strip()


def has_street_address(p: dict) -> bool:
    # A numbered street address geocodes via the batch endpoint. Cross-street
    # descriptions ("Eastern Pkwy. bet. ...") do not — those fall to centroid.
    return bool(first_zip(p)) and (p.get("address") or "").strip()[:1].isdigit()


def centroid(geom: dict | None) -> tuple[float, float] | None:
    """Rough centroid = mean of all NYC-range vertices in a (Multi)Polygon.
    Good enough to land in/near a park for NTA assignment; no GIS dep."""
    if not geom:
        return None
    xs: list[float] = []
    ys: list[float] = []

    def walk(node):
        if (
            isinstance(node, list)
            and len(node) == 2
            and all(isinstance(v, (int, float)) for v in node)
        ):
            x, y = node
            if -75 < x < -73 and 40 < y < 41:
                xs.append(x)
                ys.append(y)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(geom.get("coordinates"))
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def geocode_batch(rows: list[tuple[str, dict]]) -> dict[str, str]:
    """rows = [(uid, parkdict), ...] -> {uid: tract_geoid}."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for uid, p in rows:
        w.writerow([uid, p["address"], BORO_CITY[p["borough"]], "NY", first_zip(p)])
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(
            BATCH_URL,
            files={"addressFile": ("parks.csv", buf.getvalue(), "text/csv")},
            data={"benchmark": "Public_AR_Current", "vintage": "Census2020_Current"},
        )
        resp.raise_for_status()
    out: dict[str, str] = {}
    for rec in csv.reader(io.StringIO(resp.text)):
        # uid,input,match,matchtype,matchedaddr,lon|lat,tigerid,side,state,county,tract,block
        if len(rec) < 11 or rec[2] != "Match":
            continue
        state, county, tract = rec[8], rec[9], rec[10]
        if state and county and tract:
            out[rec[0]] = f"{state}{county}{tract}"
    return out


def reverse_tract(client: httpx.Client, lng: float, lat: float) -> str | None:
    resp = client.get(
        COORD_URL,
        params={
            "x": lng,
            "y": lat,
            "benchmark": "Public_AR_Current",
            "vintage": "Census2020_Current",
            "format": "json",
            "layers": "Census Tracts",
        },
    )
    resp.raise_for_status()
    cts = resp.json().get("result", {}).get("geographies", {}).get("Census Tracts") or []
    return cts[0]["GEOID"] if cts else None


def main() -> int:
    from nyc_events.sources._neighborhoods import normalize_name, nta_for_tract

    parks = [
        p
        for p in fetch_parks()
        if (p.get("retired") or "").lower() != "true" and p.get("borough") in BORO_CITY
    ]
    indexed = list(enumerate(parks))
    addr_parks = [(uid, p) for uid, p in indexed if has_street_address(p)]
    print(f"{len(parks)} parks; {len(addr_parks)} with street addresses")

    geoids: dict[int, str] = {}
    CHUNK = 5000
    for i in range(0, len(addr_parks), CHUNK):
        chunk = [(str(uid), p) for uid, p in addr_parks[i : i + CHUNK]]
        for uid, gid in geocode_batch(chunk).items():
            geoids[int(uid)] = gid
        print(f"  address-geocoded {min(i + CHUNK, len(addr_parks))}/{len(addr_parks)}")

    # Centroid reverse-geocode for everything the address pass didn't resolve.
    todo = [(uid, p) for uid, p in indexed if uid not in geoids]
    print(f"centroid reverse-geocoding {len(todo)} unresolved parks...")
    with httpx.Client(timeout=30.0) as client:
        for n, (uid, p) in enumerate(todo, 1):
            c = centroid(p.get("multipolygon"))
            if not c:
                continue
            try:
                gid = reverse_tract(client, c[0], c[1])
            except Exception as exc:  # noqa: BLE001 — best-effort offline build
                print(f"  reverse failed for {p.get('signname')!r}: {exc!r}", file=sys.stderr)
                continue
            if gid:
                geoids[uid] = gid
            if n % 200 == 0:
                print(f"  reverse {n}/{len(todo)}")

    table: dict[str, str] = {}
    matched = 0
    for uid, p in indexed:
        nta = nta_for_tract(geoids.get(uid))
        if not nta:
            continue
        matched += 1
        for name in (p.get("signname"), p.get("name311")):
            key = normalize_name(name)
            if key:
                table.setdefault(key, nta)

    OUT.write_text(json.dumps(table, sort_keys=True, indent=0) + "\n")
    print(f"{matched}/{len(parks)} parks resolved to an NTA; wrote {len(table)} name keys to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
