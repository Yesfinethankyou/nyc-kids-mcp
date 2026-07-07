"""Ingest CLI. Loops over enabled sources, upserts to SQLite, prunes stale.

Wired up at Checkpoint B once the NYC Parks source is implemented.
Run nightly via Synology Task Scheduler -> `docker compose run --rm ingest`.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

from . import config, db, enrich
from .sources import ENABLED_SOURCES

# Circuit breaker for missing-event marking: if a "successful" fetch returned
# less than this fraction of the source's stored future events, treat the
# fetch as silently incomplete (paginated sources soft-fail mid-run, parsers
# break wholesale on upstream redesigns) and skip marking. A real upstream
# cancellation wave never removes half a venue's calendar overnight.
MIN_FETCH_RATIO = 0.5

# Yield-drift alerting (issue #65): warn when a source fetches well below its
# recent norm — the signature of a scraper broken by an upstream change or a
# feed cap starting to truncate, which the 50% missing-detection breaker can
# sit just above. Compared against the median of recent successful runs; only
# fires once there's enough history (see db.fetch_drift_baseline).
DRIFT_RATIO = 0.6


def _fetch_looks_complete(fetched_count: int, baseline_future: int) -> bool:
    """Gate missing-event marking on the fetch looking sane vs the DB."""
    if fetched_count == 0:
        return False
    return fetched_count >= MIN_FETCH_RATIO * baseline_future


def _looks_like_drift(fetched: int, baseline: float | None) -> bool:
    """True when this run fetched suspiciously fewer rows than the source's
    recent median. Baseline None (not enough history) never alerts."""
    if baseline is None:
        return False
    return fetched < DRIFT_RATIO * baseline


def main() -> int:
    if not ENABLED_SOURCES:
        print("No sources enabled. (Checkpoint B wires up NYCParksSource.)", file=sys.stderr)
        return 1

    db_path = config.DB_PATH
    db.init_events(db_path)  # schema + migrations, off the read path (issue #28)
    total_in = 0
    total_up = 0
    failures: list[str] = []
    drift_warnings: list[str] = []
    run_id = datetime.now(UTC).isoformat()  # groups this run's per-source rows
    with db.connect_events(db_path) as conn:
        for cls in ENABLED_SOURCES:
            src = cls()
            run_start = datetime.now(UTC)
            # Baseline reflects PRIOR runs only — read before recording this one.
            drift_baseline = db.fetch_drift_baseline(conn, src.name)
            try:
                events = list(src.fetch())
            except Exception as exc:  # noqa: BLE001 — surface the failure, keep going
                failures.append(f"{src.name}: fetch failed: {exc!r}")
                db.record_ingest_run(
                    conn, run_id=run_id, source=src.name, started_at=run_start,
                    finished_at=datetime.now(UTC), outcome="fetch_failed",
                    fetched=0, inserted=0, updated=0, marked_missing=0,
                )
                continue
            fetched = len(events)
            try:
                baseline = db.count_future_events(conn, src.name, run_start)
                ins, upd = db.upsert_events(conn, events)
            except Exception as exc:  # noqa: BLE001 — one bad source must not abort the run
                failures.append(f"{src.name}: upsert failed: {exc!r}")
                db.record_ingest_run(
                    conn, run_id=run_id, source=src.name, started_at=run_start,
                    finished_at=datetime.now(UTC), outcome="upsert_failed",
                    fetched=fetched, inserted=0, updated=0, marked_missing=0,
                )
                continue
            total_in += ins
            total_up += upd
            print(f"{src.name}: {ins} inserted, {upd} updated ({fetched} fetched)")
            # Possible-cancellation detection, full-window sources only.
            # Layered against false positives: hard fetch failures never get
            # here (continue above), silently-short fetches trip the ratio
            # breaker, and a wrong stamp self-heals on the next run that
            # re-sees the event (upsert clears missing_since).
            marked = 0
            if src.window_days is not None:
                if _fetch_looks_complete(fetched, baseline):
                    marked = db.mark_missing(
                        conn,
                        source=src.name,
                        run_start=run_start,
                        window_days=src.window_days,
                    )
                    if marked:
                        print(f"{src.name}: {marked} future events gone from upstream (flagged)")
                else:
                    print(
                        f"{src.name}: fetch looks incomplete "
                        f"({fetched} fetched vs {baseline} future rows stored) — "
                        f"skipping missing-event marking",
                        file=sys.stderr,
                    )
            db.record_ingest_run(
                conn, run_id=run_id, source=src.name, started_at=run_start,
                finished_at=datetime.now(UTC), outcome="ok",
                fetched=fetched, inserted=ins, updated=upd, marked_missing=marked,
            )
            # Yield drift is measured against the source's own recent norm, so
            # it's independent of the missing-detection breaker (which compares
            # against currently-stored future rows).
            if _looks_like_drift(fetched, drift_baseline):
                msg = (
                    f"{src.name}: yield drift — fetched {fetched} vs recent "
                    f"median {drift_baseline:.0f} (< {DRIFT_RATIO:.0%})"
                )
                print(msg, file=sys.stderr)
                drift_warnings.append(msg)
        pruned = db.prune_stale(conn, datetime.now(UTC) - timedelta(days=1))

    print(f"TOTAL: {total_in} inserted, {total_up} updated, {pruned} pruned")

    # Second pass: code neighborhoods (and backfill lat/lng). Guarded so a
    # geocoder hiccup can't fail the sources (already committed above) — but
    # the failure is surfaced via a distinct exit code (3) so the nightly
    # cron alerts instead of exiting 0. Already-coded rows keep their labels
    # across ingests (see db.upsert_events), so a failed pass only delays
    # coverage for new rows. Set ENRICH=0 to skip (dev iteration without
    # network).
    enrich_failed = False
    if os.environ.get("ENRICH", "1") != "0":
        try:
            considered, coded = enrich.run(db_path)
            print(f"ENRICH: {coded}/{considered} rows coded with a neighborhood")
        except Exception as exc:  # noqa: BLE001 — must not mask source results
            enrich_failed = True
            print(f"ENRICH: failed: {exc!r}", file=sys.stderr)

    # Exit codes (highest-precedence first): 0 = clean, 2 = one or more sources
    # failed, 3 = sources fine but the enrich pass failed, 4 = sources + enrich
    # fine but at least one source's yield dropped below its recent norm
    # (issue #65) — a heads-up that a scraper may be silently degrading.
    if failures:
        print("FAILED SOURCES:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 2
    if enrich_failed:
        return 3
    if drift_warnings:
        print("YIELD DRIFT (sources fetching below recent norm):", file=sys.stderr)
        for w in drift_warnings:
            print(f"  {w}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
