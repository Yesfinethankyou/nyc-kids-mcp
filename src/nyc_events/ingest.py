"""Ingest CLI. Loops over enabled sources, upserts to SQLite, prunes stale.

Wired up at Checkpoint B once the NYC Parks source is implemented.
Run nightly via Synology Task Scheduler -> `docker compose run --rm ingest`.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

from . import db
from .sources import ENABLED_SOURCES


def main() -> int:
    if not ENABLED_SOURCES:
        print("No sources enabled. (Checkpoint B wires up NYCParksSource.)", file=sys.stderr)
        return 1

    db_path = os.environ.get("DB_PATH", "data/events.db")
    total_in = 0
    total_up = 0
    failures: list[str] = []
    with db.connect_events(db_path) as conn:
        for cls in ENABLED_SOURCES:
            src = cls()
            try:
                events = list(src.fetch())
            except Exception as exc:  # noqa: BLE001 — surface the failure, keep going
                failures.append(f"{src.name}: {exc!r}")
                continue
            ins, upd = db.upsert_events(conn, events)
            total_in += ins
            total_up += upd
            print(f"{src.name}: {ins} inserted, {upd} updated ({len(events)} fetched)")
        pruned = db.prune_stale(conn, datetime.now(UTC) - timedelta(days=1))

    print(f"TOTAL: {total_in} inserted, {total_up} updated, {pruned} pruned")
    if failures:
        print("FAILED SOURCES:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
