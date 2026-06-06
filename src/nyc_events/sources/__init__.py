"""Source registry. Add to ENABLED_SOURCES to wire a source into nightly ingest."""

from .base import Source
from .bpl import BPLSource
from .mommy_poppins import MommyPoppinsSource
from .nyc_permitted_events import NYCPermittedEventsSource

# Phase 1 + Phase 2 sources. Remaining Phase 2 sources (Time Out NY
# Kids, Brooklyn Children's Museum) will be appended here as scrapers land.
ENABLED_SOURCES: list[type[Source]] = [NYCPermittedEventsSource, MommyPoppinsSource, BPLSource]
