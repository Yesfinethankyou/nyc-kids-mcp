"""Source ABC. Every event provider implements fetch() -> Iterable[Event]."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models import Event


class Source(ABC):
    """An event provider.

    Subclasses set a stable `name` (used as the `source` field on Events and
    in compute_id) and implement `fetch()`. Per-row parse errors should be
    caught and logged inside the source so one bad row doesn't kill the run.
    """

    name: str

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
