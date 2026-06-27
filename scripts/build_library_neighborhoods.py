"""Build src/nyc_events/data/library_neighborhoods.json.

A one-shot data-prep script (not run at ingest time). Maps NYC public-library
branch names to their neighborhood (NTA) so the enrichment pass can code
library-source rows (BPL today; QPL/NYPL when those sources land) the same way
the park table codes permit rows.

Pipeline (NYC open data, no API keys):
  1. Pull library facilities from the NYC Facilities Database (FacDB, ji82-xba5):
     name + street address + borough + ZIP + lat/lng.
  2. Batch-geocode the addresses with the US Census geocoder -> tract GEOID;
     fall back to reverse-geocoding FacDB's own lat/lng for any address miss.
  3. Map tract GEOID -> NTA via tract_to_nta.json (build_tract_nta.py first).
  4. Emit {"<borough>|<library-core>": nta_name}, where library-core strips the
     generic "library"/"branch"/... tokens so the BPL feed's "Arlington Library"
     keys the same as FacDB's "ARLINGTON LIBRARY". Borough-keyed so a "Central
     Library" in Brooklyn and Queens don't collide.

Run (after build_tract_nta.py):
    .venv/bin/python scripts/build_library_neighborhoods.py
Provenance: https://data.cityofnewyork.us/d/ji82-xba5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _census import batch_geographies, first_zip, reverse_tract  # noqa: E402

FACDB_URL = "https://data.cityofnewyork.us/resource/ji82-xba5.json"
OUT = ROOT / "src" / "nyc_events" / "data" / "library_neighborhoods.json"


def fetch_libraries() -> list[dict]:
    with httpx.Client(timeout=120.0) as client:
        resp = client.get(
            FACDB_URL,
            params={
                "$where": "upper(factype) like '%LIBRARY%'",
                "$select": "facname,boro,address,city,zipcode,latitude,longitude",
                "$limit": 50000,
            },
        )
        resp.raise_for_status()
        return resp.json()


def usable(lib: dict) -> bool:
    return bool(lib.get("facname")) and bool(lib.get("boro"))


def main() -> int:
    from nyc_events.sources._neighborhoods import library_core, normalize_name, nta_for_tract

    libs = [lib for lib in fetch_libraries() if usable(lib)]
    indexed = list(enumerate(libs))
    print(f"{len(libs)} library facilities")

    addr = [
        (str(uid), lib["address"], lib.get("city") or lib["boro"], "NY",
         first_zip(lib.get("zipcode")))
        for uid, lib in indexed
        if (lib.get("address") or "").strip()[:1].isdigit() and first_zip(lib.get("zipcode"))
    ]
    geoids: dict[int, str] = {int(uid): gid for uid, gid in batch_geographies(addr).items()}
    print(f"  address-geocoded {len(geoids)}/{len(libs)}")

    # FacDB ships lat/lng; reverse-geocode it for anything the address pass missed.
    with httpx.Client(timeout=30.0) as client:
        for uid, lib in indexed:
            if uid in geoids:
                continue
            try:
                lat, lng = float(lib["latitude"]), float(lib["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                gid = reverse_tract(client, lng, lat)
            except Exception as exc:  # noqa: BLE001 — best-effort offline build
                print(f"  reverse failed for {lib.get('facname')!r}: {exc!r}", file=sys.stderr)
                continue
            if gid:
                geoids[uid] = gid

    table: dict[str, str] = {}
    matched = 0
    for uid, lib in indexed:
        nta = nta_for_tract(geoids.get(uid))
        if not nta:
            continue
        matched += 1
        key = f"{normalize_name(lib['boro'])}|{library_core(lib['facname'])}"
        table.setdefault(key, nta)

    OUT.write_text(json.dumps(table, sort_keys=True, indent=0) + "\n")
    print(f"{matched}/{len(libs)} libraries resolved; wrote {len(table)} keys to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
