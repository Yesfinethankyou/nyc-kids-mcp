"""Build src/nyc_events/data/tract_to_nta.json.

A one-shot data-prep script (not run at ingest time). Pulls the authoritative
NYC DCP "2020 Census Tracts to 2020 NTAs and CDTAs Equivalency" table
(Socrata dataset hm78-6dwm) and writes a compact {tract_geoid: nta_name} map.

The 11-digit `geoid` here is exactly the GEOID the US Census geocoder returns
under `Census Tracts` / the batch geocoder's STATE+COUNTY+TRACT columns, so the
runtime can go geocode -> tract GEOID -> neighborhood with a single dict lookup.

Run: .venv/bin/python scripts/build_tract_nta.py
Provenance: https://data.cityofnewyork.us/d/hm78-6dwm
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

DATASET_URL = "https://data.cityofnewyork.us/resource/hm78-6dwm.json"
OUT = Path(__file__).resolve().parents[1] / "src" / "nyc_events" / "data" / "tract_to_nta.json"


def main() -> int:
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(DATASET_URL, params={"$select": "geoid,ntaname", "$limit": 50000})
        resp.raise_for_status()
        rows = resp.json()

    mapping = {
        r["geoid"]: r["ntaname"]
        for r in rows
        if r.get("geoid") and r.get("ntaname")
    }
    OUT.write_text(json.dumps(mapping, sort_keys=True, indent=0) + "\n")
    print(f"wrote {len(mapping)} tract->nta entries to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
