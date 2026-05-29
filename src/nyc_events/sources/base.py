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

    @abstractmethod
    def fetch(self) -> Iterable[Event]: ...
