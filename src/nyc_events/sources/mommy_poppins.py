"""Mommy Poppins NYC family events. Phase 2 — not implemented."""

from collections.abc import Iterable

from ..models import Event
from .base import Source


class MommyPoppinsSource(Source):
    name = "mommy_poppins"

    def fetch(self) -> Iterable[Event]:  # Phase 2
        raise NotImplementedError("Phase 2 source")
