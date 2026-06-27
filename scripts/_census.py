"""Shared US Census geocoder helpers for the one-shot data-prep scripts.

Not imported by the app at runtime (the runtime path is src/nyc_events/geocode.py
+ the geocode_cache). These are the bulk/offline primitives the build_*.py
scripts use to turn NYC open-data addresses/points into 2020 census tract
GEOIDs, which the tract_to_nta.json crosswalk then maps to NTA neighborhoods.
"""

from __future__ import annotations

import csv
import io

import httpx

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
COORD_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
BENCHMARK = "Public_AR_Current"
VINTAGE = "Census2020_Current"


def first_zip(zipcode: str | None) -> str:
    # Some open-data rows list several ZIPs ("11364, 11423"); the batch CSV
    # needs one, and the Census matcher leans on the street range anyway.
    return (zipcode or "").split(",")[0].strip()


def batch_geographies(rows: list[tuple[str, str, str, str, str]]) -> dict[str, str]:
    """rows = [(uid, street, city, state, zip), ...] -> {uid: tract_geoid}."""
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(
            BATCH_URL,
            files={"addressFile": ("in.csv", buf.getvalue(), "text/csv")},
            data={"benchmark": BENCHMARK, "vintage": VINTAGE},
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
            "benchmark": BENCHMARK,
            "vintage": VINTAGE,
            "format": "json",
            "layers": "Census Tracts",
        },
    )
    resp.raise_for_status()
    cts = resp.json().get("result", {}).get("geographies", {}).get("Census Tracts") or []
    return cts[0]["GEOID"] if cts else None
