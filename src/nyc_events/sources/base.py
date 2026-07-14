"""Source ABC. Every event provider implements fetch() -> Iterable[Event]."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models import Event


class Source(ABC):
    """An event provider.

    Subclasses set a stable `name` (used as the `source` field on Events and
    in compute_id) and a human-friendly `display_name`, then implement
    `fetch()`. Per-row parse errors should be caught and logged inside the
    source so one bad row doesn't kill the run.
    """

    # Stable machine id: the `events.source` column and a compute_id input.
    # NEVER rename an existing slug — it would change every row's stable id.
    name: str

    # Human-friendly label for display (the MCP list_sources / get_event_detail
    # `source_name`, the tailnet dashboard). Every source MUST set this — the
    # slug above is not meant for people to read. Use the venue/brand as a
    # reader would say it ("Queens Public Library", not "qpl"). The registry
    # (sources/__init__.py) collects these into SOURCE_DISPLAY_NAMES and a test
    # enforces that every source defines one, so a new source can't ship without
    # a friendly name.
    display_name: str

    # Opt-in to missing-event (possible-cancellation) detection. Set this
    # (usually in __init__, mirroring the fetch window) ONLY if every fetch()
    # is a full re-fetch of all events from now through now+window_days —
    # then "in-window future event not in this fetch" means upstream removed
    # it. Leave None for incremental sources (e.g. mommy_poppins discovers
    # via sitemap lastmod, so an unmodified event page legitimately drops out
    # of a run while the event is still on). See ingest.py / db.mark_missing.
    window_days: int | None = None

    @abstractmethod
    def fetch(self) -> Iterable[Event]: ...
