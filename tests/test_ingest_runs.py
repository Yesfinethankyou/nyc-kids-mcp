"""Ingest telemetry + yield-drift alerting (issue #65).

Unit tests for the db helpers and the drift predicate, plus an integration
test that drives ingest.main with a fake source (no network) to exercise the
per-source run recording and the exit-code-4 drift path.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from nyc_events import config, db, ingest
from nyc_events.models import Event
from nyc_events.sources.base import Source


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_events(path)
    with db.connect_events(path) as c:
        yield c


def _record(conn, source, fetched, outcome="ok"):
    start = datetime.now(UTC)
    db.record_ingest_run(
        conn, run_id=start.isoformat(), source=source, started_at=start,
        finished_at=start + timedelta(seconds=1), outcome=outcome,
        fetched=fetched, inserted=fetched, updated=0, marked_missing=0,
    )


# --- db helpers --------------------------------------------------------------


def test_record_and_duration(conn):
    start = datetime(2026, 7, 7, 3, 0, tzinfo=UTC)
    db.record_ingest_run(
        conn, run_id="r1", source="s", started_at=start,
        finished_at=start + timedelta(seconds=12), outcome="ok",
        fetched=5, inserted=5, updated=0, marked_missing=0,
    )
    row = conn.execute("SELECT * FROM ingest_runs").fetchone()
    assert row["source"] == "s"
    assert row["fetched"] == 5
    assert row["duration_s"] == pytest.approx(12.0)


def test_drift_baseline_needs_min_history(conn):
    _record(conn, "s", 10)
    _record(conn, "s", 10)
    # Only two prior runs — below the default min_history of 3.
    assert db.fetch_drift_baseline(conn, "s") is None
    _record(conn, "s", 10)
    assert db.fetch_drift_baseline(conn, "s") == 10


def test_drift_baseline_is_median_of_recent_ok_runs(conn):
    for n in (8, 10, 12):
        _record(conn, "s", n)
    assert db.fetch_drift_baseline(conn, "s") == 10


def test_drift_baseline_ignores_failed_runs(conn):
    _record(conn, "s", 0, outcome="fetch_failed")
    _record(conn, "s", 10)
    _record(conn, "s", 10)
    # Two 'ok' runs + one failure -> still below min_history of 3 'ok' runs.
    assert db.fetch_drift_baseline(conn, "s") is None


def test_drift_baseline_is_per_source(conn):
    for _ in range(3):
        _record(conn, "a", 100)
    assert db.fetch_drift_baseline(conn, "b") is None


@pytest.mark.parametrize(
    "fetched, baseline, expected",
    [
        (2, 10.0, True),    # well below the 60% floor
        (6, 10.0, False),   # exactly at the floor is not drift
        (9, 10.0, False),
        (0, 10.0, True),
        (5, None, False),   # no history -> never alert
    ],
)
def test_looks_like_drift(fetched, baseline, expected):
    assert ingest._looks_like_drift(fetched, baseline) is expected


# --- integration: ingest.main wiring -----------------------------------------


_FAKE = {"n": 10, "raise": False}


class _FakeSource(Source):
    name = "fake"
    window_days = None  # keep missing-detection out of this test

    def fetch(self) -> Iterable[Event]:
        if _FAKE["raise"]:
            raise RuntimeError("boom")
        base = datetime.now(UTC)
        for i in range(_FAKE["n"]):
            yield Event(
                id=f"fake-{i}",
                source="fake",
                title=f"Event {i}",
                start_dt=base + timedelta(days=i + 1),
            )


@pytest.fixture
def fake_ingest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "events.db"))
    monkeypatch.setattr(ingest, "ENABLED_SOURCES", [_FakeSource])
    monkeypatch.setenv("ENRICH", "0")  # no geocoder network in tests
    _FAKE.update({"n": 10, "raise": False})
    return tmp_path


def test_main_records_a_run_per_source(fake_ingest):
    assert ingest.main() == 0
    with db.connect_events(config.DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM ingest_runs").fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "fake"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["fetched"] == 10


def test_main_flags_yield_drift_with_exit_code_4(fake_ingest):
    # Build a stable history, then a collapsed fetch.
    _FAKE["n"] = 10
    for _ in range(3):
        assert ingest.main() == 0  # not enough history yet / stable -> clean
    _FAKE["n"] = 2
    assert ingest.main() == 4      # 2 < 60% of median(10) -> drift
    with db.connect_events(config.DB_PATH) as conn:
        last = conn.execute(
            "SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert last["fetched"] == 2


def test_main_records_fetch_failure_and_returns_2(fake_ingest):
    _FAKE["raise"] = True
    try:
        assert ingest.main() == 2
        with db.connect_events(config.DB_PATH) as conn:
            row = conn.execute("SELECT * FROM ingest_runs").fetchone()
        assert row["outcome"] == "fetch_failed"
        assert row["fetched"] == 0
    finally:
        _FAKE["raise"] = False
