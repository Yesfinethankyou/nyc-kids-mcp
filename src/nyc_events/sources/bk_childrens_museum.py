"""Brooklyn Children's Museum events. Phase 2 — not implemented."""

from collections.abc import Iterable

from ..models import Event
from .base import Source


class BrooklynChildrensMuseumSource(Source):
    name = "bk_childrens_museum"

    def fetch(self) -> Iterable[Event]:  # Phase 2
        raise NotImplementedError("Phase 2 source")
