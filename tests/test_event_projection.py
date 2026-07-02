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


def test_summary_neighborhood_is_none_when_unset():
    assert _event_summary(_ev(neighborhood=None))["neighborhood"] is None


def test_detail_still_includes_neighborhood():
    assert _event_detail(_ev())["neighborhood"] == "Williamsburg"
