"""Tests for the events_this_weekend window calculation.

Regression coverage for the bug where the window started at `now` instead
of Saturday 00:00, so a Monday query returned Tuesday–Friday events as
"this weekend".
"""

from __future__ import annotations

from datetime import datetime

from nyc_events.tools import NYC_TZ, _weekend_window


def _local(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=NYC_TZ)


# 2026-06-08 is a Monday; 2026-06-13/14 are Saturday/Sunday.


def test_midweek_window_starts_saturday_not_now():
    start, end = _weekend_window(_local(2026, 6, 8, 10, 30))  # Monday
    assert start == _local(2026, 6, 13)  # Saturday 00:00, not Monday 10:30
    assert end == _local(2026, 6, 14, 23, 59).replace(second=59)


def test_friday_window_excludes_friday_evening():
    start, _ = _weekend_window(_local(2026, 6, 12, 18, 0))  # Friday 6pm
    assert start == _local(2026, 6, 13)


def test_saturday_window_starts_now():
    now = _local(2026, 6, 13, 14, 0)  # Saturday 2pm
    start, end = _weekend_window(now)
    assert start == now  # rest of today, not this morning
    assert end == _local(2026, 6, 14, 23, 59).replace(second=59)


def test_sunday_window_starts_now_and_ends_tonight():
    now = _local(2026, 6, 14, 9, 0)  # Sunday 9am
    start, end = _weekend_window(now)
    assert start == now
    assert end == _local(2026, 6, 14, 23, 59).replace(second=59)
