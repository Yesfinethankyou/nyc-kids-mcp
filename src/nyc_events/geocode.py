"""US Census geocoder client (no API key).

Forward (one-line address -> point + 2020 census tract GEOID) and reverse
(point -> tract GEOID). Used only by the enrichment pass — sources never touch
the network for location data. The tract GEOID is then mapped to an NTA
neighborhood name via sources._neighborhoods.nta_for_tract.
"""

from __future__ import annotations

import httpx

_BASE = "https://geocoding.geo.census.gov/geocoder/geographies"
_BENCHMARK = "Public_AR_Current"
_VINTAGE = "Census2020_Current"


def forward(query: str, *, client: httpx.Client) -> tuple[float, float, str] | None:
    """One-line address -> (lat, lng, tract_geoid), or None if unmatched."""
    resp = client.get(
        f"{_BASE}/onelineaddress",
        params={
            "address": query,
            "benchmark": _BENCHMARK,
            "vintage": _VINTAGE,
            "format": "json",
            "layers": "Census Tracts",
        },
    )
    resp.raise_for_status()
    matches = resp.json().get("result", {}).get("addressMatches") or []
    if not matches:
        return None
    m = matches[0]
    coords = m.get("coordinates") or {}
    tracts = (m.get("geographies") or {}).get("Census Tracts") or []
    if not tracts or coords.get("y") is None or coords.get("x") is None:
        return None
    return float(coords["y"]), float(coords["x"]), tracts[0].get("GEOID")


def reverse(lat: float, lng: float, *, client: httpx.Client) -> str | None:
    """(lat, lng) -> tract_geoid, or None if the point isn't in NYC tracts."""
    resp = client.get(
        f"{_BASE}/coordinates",
        params={
            "x": lng,
            "y": lat,
            "benchmark": _BENCHMARK,
            "vintage": _VINTAGE,
            "format": "json",
            "layers": "Census Tracts",
        },
    )
    resp.raise_for_status()
    tracts = resp.json().get("result", {}).get("geographies", {}).get("Census Tracts") or []
    return tracts[0].get("GEOID") if tracts else None
