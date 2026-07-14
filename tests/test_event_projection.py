"""Tool output projections (tools._event_summary / _event_detail).

Guards the token-efficient summary shape, specifically that neighborhood is
now surfaced in list results (it was previously detail-only).
"""

from __future__ import annotations

from datetime import UTC, datetime

from nyc_events.models import Borough, Event, Price
from nyc_events.tools import _event_detail, _event_summary


def _ev(**kw):
    base = dict(
        id="abc123", source="domino_park", title="Kids Day",
        start_dt=datetime(2026, 7, 1, 14, tzinfo=UTC),
        venue_name="Domino Park", borough=Borough.BROOKLYN,
        neighborhood="Williamsburg", price=Price.UNKNOWN, tags=["family"],
    )
    base.update(kw)
    return Event(**base)


def test_summary_includes_neighborhood():
    summary = _event_summary(_ev())
    assert summary["neighborhood"] == "Williamsburg"
    assert summary["borough"] == "Brooklyn"


def test_summary_includes_end_local_when_present():
    # A noon–4pm event must present as a range, not a bare "12:00" that
    # reads as midnight. NYC local: 14:00 UTC == 10:00 EDT.
    summary = _event_summary(_ev(end_dt=datetime(2026, 7, 1, 18, tzinfo=UTC)))
    assert summary["when_local"] == "2026-07-01T10:00:00-04:00"
    assert summary["end_local"] == "2026-07-01T14:00:00-04:00"


def test_summary_end_local_is_none_when_source_has_no_end():
    assert _event_summary(_ev())["end_local"] is None


def test_summary_neighborhood_is_none_when_unset():
    assert _event_summary(_ev(neighborhood=None))["neighborhood"] is None


def test_detail_still_includes_neighborhood():
    assert _event_detail(_ev())["neighborhood"] == "Williamsburg"


def test_detail_includes_friendly_source_name():
    detail = _event_detail(_ev())
    assert detail["source"] == "domino_park"  # stable id unchanged
    assert detail["source_name"] == "Domino Park"  # friendly label alongside


def test_detail_source_name_falls_back_to_slug_when_unmapped():
    # An unknown/retired slug never breaks the projection — it echoes the slug.
    assert _event_detail(_ev(source="some_future_source"))["source_name"] == (
        "some_future_source"
    )
