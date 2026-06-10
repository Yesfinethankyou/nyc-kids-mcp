"""Source registry. Add to ENABLED_SOURCES to wire a source into nightly ingest."""

from .base import Source
from .bk_childrens_museum import BrooklynChildrensMuseumSource
from .bpl import BPLSource
from .greenwood_cemetery import GreenWoodCemeterySource
from .mommy_poppins import MommyPoppinsSource
from .ny_transit_museum import NYTransitMuseumSource
from .nyc_permitted_events import NYCPermittedEventsSource
from .prospect_park import ProspectParkSource

# Phase 1 + Phase 2 sources. Time Out NY Kids (timeout_nykids.py) is a
# JS-rendered editorial site with no event feed — not buildable without a
# headless browser; stub left in place but not enabled.
ENABLED_SOURCES: list[type[Source]] = [
    NYCPermittedEventsSource,
    MommyPoppinsSource,
    BPLSource,
    BrooklynChildrensMuseumSource,
    GreenWoodCemeterySource,
    ProspectParkSource,
    NYTransitMuseumSource,
]
