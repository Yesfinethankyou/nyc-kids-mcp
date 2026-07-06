"""Source registry. Add to ENABLED_SOURCES to wire a source into nightly ingest."""

from .base import Source
from .bk_childrens_museum import BrooklynChildrensMuseumSource
from .bpl import BPLSource
from .brooklyn_army_terminal import BrooklynArmyTerminalSource
from .domino_park import DominoParkSource
from .governors_island import GovernorsIslandSource
from .greenwood_cemetery import GreenWoodCemeterySource
from .industry_city import IndustryCitySource
from .mommy_poppins import MommyPoppinsSource
from .ny_transit_museum import NYTransitMuseumSource
from .nyc_permitted_events import NYCPermittedEventsSource
from .nycgovparks_events import NYCGovParksEventsSource
from .prospect_park import ProspectParkSource

# Phase 1 + Phase 2 sources. Time Out NY Kids (timeout_nykids.py) is a
# JS-rendered editorial site with no event feed — not buildable without a
# headless browser; stub left in place but not enabled.
#
# Order matters: the loop in ingest.py is strictly sequential with no
# per-source time budget, so a slow source starves everything after it. We
# run the cheap sources (seconds each) first and the expensive crawls last,
# so an interrupted run (Watchtower restart, killed `docker exec`) still gets
# the quick wins in. mommy_poppins is last: it crawls ~700 detail pages and
# is by far the longest single source.
ENABLED_SOURCES: list[type[Source]] = [
    NYTransitMuseumSource,
    BrooklynArmyTerminalSource,
    BrooklynChildrensMuseumSource,
    GreenWoodCemeterySource,
    ProspectParkSource,
    IndustryCitySource,
    GovernorsIslandSource,
    DominoParkSource,
    NYCPermittedEventsSource,
    BPLSource,
    # ~49 list pages at a 1s polite delay (~1 min) — expensive-ish, so it runs
    # with the other slow crawls at the end; mommy_poppins stays last (see the
    # ordering note above).
    NYCGovParksEventsSource,
    MommyPoppinsSource,
]
