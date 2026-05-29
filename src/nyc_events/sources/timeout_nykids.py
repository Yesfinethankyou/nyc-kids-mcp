"""Time Out NY Kids events. Phase 2 — not implemented."""

from collections.abc import Iterable

from ..models import Event
from .base import Source


class TimeOutNYKidsSource(Source):
    name = "timeout_nykids"

    def fetch(self) -> Iterable[Event]:  # Phase 2
        raise NotImplementedError("Phase 2 source")
