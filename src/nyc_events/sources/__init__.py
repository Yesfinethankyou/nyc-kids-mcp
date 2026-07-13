"""Source registry. Add to ENABLED_SOURCES to wire a source into nightly ingest."""

from .base import Source
from .bbg import BBGSource
from .bk_childrens_museum import BrooklynChildrensMuseumSource
from .bpl import BPLSource
from .brooklyn_army_terminal import BrooklynArmyTerminalSource
from .brooklyn_bridge_park import BrooklynBridgeParkSource
from .city_parks_foundation import CityParksFoundationSource
from .domino_park import DominoParkSource
from .governors_island import GovernorsIslandSource
from .greenwood_cemetery import GreenWoodCemeterySource
from .industry_city import IndustryCitySource
from .intrepid import IntrepidSource
from .mommy_poppins import MommyPoppinsSource
from .new_york_family import NewYorkFamilySource
from .ny_transit_museum import NYTransitMuseumSource
from .nycgovparks_events import NYCGovParksEventsSource
from .nypl import NYPLSource
from .prospect_park import ProspectParkSource
from .qpl import QPLSource
from .si_childrens_museum import SIChildrensMuseumSource
from .snug_harbor import SnugHarborSource

# Phase 2 sources. Time Out NY Kids (timeout_nykids.py) is a JS-rendered
# editorial site with no event feed — not buildable without a headless
# browser; stub left in place but not enabled.
#
# nyc_permitted_events (tvpp-9vvx, the Phase 1 permit registry) was DISABLED
# 2026-07-12 by maintainer decision: every row it yields is low-confidence
# (no description, no URL) and nycgovparks_events now covers the curated
# Parks calendar, so the permit rows were unused noise. Module + tests kept
# for easy re-enable; see its module docstring.
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
    # ~2 REST pages at a 1s delay — cheap.
    CityParksFoundationSource,
    # ~15-20 calendar pages at a 1s delay — cheap-ish.
    IntrepidSource,
    SIChildrensMuseumSource,
    # 2-3 month pages at a 1s delay — cheap.
    BBGSource,
    # ~8 REST pages at a 0.75s delay — cheap-ish.
    BrooklynBridgeParkSource,
    BPLSource,
    # ~49 list pages at a 1s polite delay (~1 min) — expensive-ish, so it runs
    # with the other slow crawls at the end; mommy_poppins stays last (see the
    # ordering note above).
    NYCGovParksEventsSource,
    # Crawls every youth/family event's detail page for its JSON-LD date
    # (~150 detail fetches at a 0.5s delay, ~1.5 min) — slow, so it runs with
    # the crawls; mommy_poppins stays last (see the ordering note above).
    SnugHarborSource,
    # Three boroughs paginated (~160 listing pages at a 1s delay, ~3 min) —
    # slow, so it runs with the crawls; mommy_poppins stays last.
    NYPLSource,
    # ~98 listing pages at a 1s delay (~1.6 min) — slow-ish, runs with the crawls.
    QPLSource,
    # Day-walk over a capped API: ~35 base requests plus extra slices on busy
    # days (~50-120 total at a 0.75s delay) — slow-ish, so it runs with the
    # crawls; mommy_poppins stays last (see the ordering note above).
    NewYorkFamilySource,
    MommyPoppinsSource,
]
