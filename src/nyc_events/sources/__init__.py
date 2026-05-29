"""Source registry. Add to ENABLED_SOURCES to wire a source into nightly ingest."""

from .base import Source
from .nyc_permitted_events import NYCPermittedEventsSource

# Phase 1 source. Phase 2 sources (Mommy Poppins, BPL, Time Out NY Kids,
# Brooklyn Children's Museum) will be appended here as scrapers land.
ENABLED_SOURCES: list[type[Source]] = [NYCPermittedEventsSource]
