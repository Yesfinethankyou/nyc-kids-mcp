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

# Circuit breaker for missing-event marking: if a "successful" fetch returned
# less than this fraction of the source's stored future events, treat the
# fetch as silently incomplete (paginated sources soft-fail mid-run, parsers
# break wholesale on upstream redesigns) and skip marking. A real upstream
# cancellation wave never removes half a venue's calendar overnight.
MIN_FETCH_RATIO = 0.5


def _fetch_looks_complete(fetched_count: int, baseline_future: int) -> bool:
    """Gate missing-event marking on the fetch looking sane vs the DB."""
    if fetched_count == 0:
        return False
    return fetched_count >= MIN_FETCH_RATIO * baseline_future


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
            run_start = datetime.now(UTC)
            try:
                events = list(src.fetch())
            except Exception as exc:  # noqa: BLE001 — surface the failure, keep going
                failures.append(f"{src.name}: {exc!r}")
                continue
            baseline = db.count_future_events(conn, src.name, run_start)
            ins, upd = db.upsert_events(conn, events)
            total_in += ins
            total_up += upd
            print(f"{src.name}: {ins} inserted, {upd} updated ({len(events)} fetched)")
            # Possible-cancellation detection, full-window sources only.
            # Layered against false positives: hard fetch failures never get
            # here (continue above), silently-short fetches trip the ratio
            # breaker, and a wrong stamp self-heals on the next run that
            # re-sees the event (upsert clears missing_since).
            if src.window_days is None:
                continue
            if not _fetch_looks_complete(len(events), baseline):
                print(
                    f"{src.name}: fetch looks incomplete "
                    f"({len(events)} fetched vs {baseline} future rows stored) — "
                    f"skipping missing-event marking",
                    file=sys.stderr,
                )
                continue
            marked = db.mark_missing(
                conn,
                source=src.name,
                run_start=run_start,
                window_days=src.window_days,
            )
            if marked:
                print(f"{src.name}: {marked} future events gone from upstream (flagged)")
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
