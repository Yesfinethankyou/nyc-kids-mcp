# Phase 3 plan

Working plan for Phase 3. Phase 2 shipped the editorial-source set and cleared
its buildable backlog (see `SOURCES-BACKLOG.md`). Phase 3 makes events
**location-aware**, **expands venue coverage**, and pays down deferred
tech debt.

**Explicitly out of scope for Phase 3:** AI/LLM enrichment (age inference,
indoor/outdoor classification, cross-source dedup, semantic search). Revisit
in a later phase. Indoor/outdoor here is heuristic, not model-derived (see A2).

## Definition of done

- Geocoding + neighborhood backfilled for all rows that can be located.
- Weather attached to upcoming **outdoor** events inside the forecast window.
- Indoor/outdoor flag on every event (heuristic + per-source default).
- The Phase-3 venue list below is each BUILT or REJECTED (source-adder recipe).
- Deferred issues #4, #5, #6 closed.
- Enrichment runs as a second nightly pass with a caching layer; source
  `fetch()` stays as simple as it is today.

---

## Decision: headless browser (Playwright) — STRUCK (2026-07-07 review)

**Resolved: not adopting headless, not even as the contained fallback.**
Maintainer call after the 2026-07-07 architectural review. The benefit side of
the ledger is thin: every named Phase-3 venue is *expected* to have a
structured feed, and the real Phase-2 lesson was "re-probe with `curl_cffi`
impersonation before believing 'no feed'" (three sources were rescued that
way), not "we need Chromium". The rejected JS venues (Time Out, the Cyclones'
themed nights) are nice-to-haves. A standing capability maintained for
hypothetical sources is exactly the over-engineering this project otherwise
avoids. **Re-open only when a specific, high-value source has survived a full
probe and demonstrably needs rendering.** The cost analysis below is kept for
that future decision's record.

None of the Phase-3 venues below are known to require JS rendering — they're
zoos / museums / libraries / parks that are probably plain REST / JSON-LD /
ICS. So headless is **not a thing we build sources for**; it's a fallback in
the source-adder toolkit for whichever of them turns out to be JS-only (and it
also unblocks the Phase-2-rejected JS venues if we ever want them).

**Cost — weight:** Chromium adds ~300–450 MB to the image (~3× today's slim
image); each page render peaks at ~300–700 MB RAM (the number that matters on
the NAS).

**Cost — complexity (three real ones):**
1. Dockerfile: `playwright install --with-deps chromium` pulls many system
   libs and pokes at our hardening (non-root uid 10001; writes only to
   `/data`) — Chromium needs a writable browser dir + `/tmp`. Multi-arch
   (amd64 + arm64) builds get heavier/slower.
2. Runtime discipline: renders are slow + flaky → explicit `networkidle` /
   selector waits, per-page timeout, one retry, and a guaranteed
   `browser.close()` (or we leak Chromium processes).
3. Ops: spinning Chromium up during the nightly run.

**What contains it (why it's cheap *here* specifically):**
- The fetch/parse split already in place means headless only changes
  `fetch()`. The fixture is the **rendered** HTML, parsed exactly like today —
  **parser tests are unchanged.**
- `fetch()` is sync and Playwright has a sync API → no async refactor. One
  shared `_render(url) -> html` helper, used **opt-in by only the sources that
  need it**; every other source stays httpx / `curl_cffi`.
- **Split the image (recommended).** The always-on, public, hardened *server*
  container never needs Chromium — only the nightly *ingest* does. Put
  Playwright in a separate ingest image; the server image stays exactly as
  lean and hardened as today, and the public attack surface is unchanged.
  Tradeoff: ingest changes from `docker exec nyc-events …` (into the server
  container) to `docker compose run --rm ingest` against the ingest image — a
  small DSM Task Scheduler change.

**Original recommendation (superseded by the STRUCK decision above):** adopt
it as (a) opt-in per source, (b) a sync `_render` helper, (c) a separate
ingest image. The one piece that survives on its own merits: **the separate
ingest compose service is happening anyway** (issue #68) — it removes the
Watchtower-kills-mid-run race regardless of headless, and it's just a compose
stanza + DSM Task Scheduler edit with no image changes.

---

## Workstream A — Location enrichment

Dependency chain: **geocode → (neighborhood, weather)**, and
**indoor/outdoor → weather relevance**. Build A1 first.

### A1. Geocode + neighborhood — **DONE**

Shipped as the `enrich.py` second pass. See CLAUDE.md "Neighborhood coding" for
the as-built detail. Resolved the open decisions below: **geocoder = US Census**
(no key; tract GEOID → NTA via the committed `tract_to_nta.json` crosswalk —
Nominatim wasn't needed); **cache = a `geocode_cache` table in `events.db`**
(no TTL). Neighborhood now resolves through a 5-tier ladder (fixed-venue
constant → enumerable site → open-data park table → reverse-geocode → forward-
geocode), and `lat`/`lng` are backfilled as a side effect. Surfaced in the
`search_events` summary + a `neighborhood` substring filter.

**`near_me` / sort-by-distance — declined, out of scope.** The coords A1
produces would support it, but the feature itself isn't wanted. Not tracked
as remaining A1 work; revisit only if requirements change.

- ~~Backfill `lat`/`lng` for rows lacking coords~~ — done (forward-geocode tier
  fills coords when it resolves a venue; existing source coords are never
  clobbered).
- Geocoder: **US Census geocoder** (free, no key, no rate pain, strong on NYC
  street addresses) as primary; Nominatim as fallback. Geocode by
  `venue_name + borough + "NY"` (same string we already build `venue_map_url`
  from).
- Reverse-geocode / map to `neighborhood` (the `Event.neighborhood` field
  already exists but is mostly null). NYC NTA (Neighborhood Tabulation Areas)
  or a borough+ZIP→neighborhood table.
- **Cache** geocode results by the lookup string — venue locations are stable,
  never re-hit upstream for a venue we've seen (see caching layer below).

### A2. Indoor/outdoor flag (heuristic — NOT AI)
- Per-source default: museums/libraries → indoor; zoos/gardens/parks →
  outdoor; mixed venues (e.g. Intrepid, botanic gardens with conservatories)
  → `mixed`/unknown.
- Keyword heuristic on title/description as an override ("rooftop", "garden",
  "indoor pool", "gallery", …).
- Store as an enum field (`indoor` / `outdoor` / `mixed` / `unknown`). New
  nullable column + idempotent migration (follows the existing `ALTER TABLE
  ADD COLUMN` pattern).

### A3. Weather
- Source: **NWS `api.weather.gov`** (free, no key). Only meaningful for
  **outdoor**/**mixed** events (depends on A2) within the forecast window
  (~7 days).
- **Keyed by `neighborhood`, NOT by per-event/per-venue coordinates —
  settled.** A citywide catalog at NWS's own grid resolution (~2.5km cells)
  gains nothing from per-venue precision — "rain likely Saturday" doesn't
  change block to block. More importantly, a meaningful slice of the catalog
  gets its `neighborhood` from the **offline enrich tiers** (fixed-venue
  constant, park-name table, library table — see "Neighborhood coding" in
  CLAUDE.md) *without ever resolving lat/lng*, so keying off per-event coords
  would silently skip those rows. Keying off the neighborhood string covers
  every row that has one, coords or not, and collapses the lookup volume from
  one-per-venue to ~150–200 total (the distinct neighborhood labels in use).
  - **New one-time offline build** (same recipe as `build_tract_nta.py` /
    `build_park_neighborhoods.py`): `scripts/build_neighborhood_centroids.py`
    → `data/neighborhood_centroids.json`, one representative point per
    neighborhood label actually seen in the catalog.
  - **Two-tier cache**, same shape as `geocode_cache`: `neighborhood →
    NWS gridpoint` (stable, no TTL — the mapping never changes) and
    `gridpoint → forecast` (short TTL, a few hours, refreshed lazily).
  - Events with `neighborhood IS NULL` get no weather — same "`None` is the
    status quo" pattern as the rest of the enrich pipeline; not worth a
    fallback.
  - Tradeoff accepted: an event near a neighborhood's edge samples weather
    from that neighborhood's centroid, which could be off by up to
    ~a mile in a large neighborhood. Irrelevant at city-forecast granularity.
- Tool output: attach a compact forecast (temp range + precip/condition) to
  applicable events; Claude can warn "rain likely Saturday."

### Architecture: second nightly pass + caching layer
- Keep source `fetch()` dumb. Add an **enrichment step** that runs after
  ingest (same nightly job, second phase): geocode missing coords → attach
  neighborhood → fetch weather for upcoming outdoor/mixed events by
  neighborhood.
- **Caching layer:** a geocode cache (stable, no TTL — key = lookup string),
  a neighborhood→gridpoint cache (stable, no TTL — key = neighborhood name),
  and a weather cache (short TTL — key = NWS gridpoint). Decision: new tables
  in `events.db` vs. a third SQLite file. Leaning new tables in `events.db`
  (it's event-derived data), kept clearly namespaced.

---

## Workstream B — New venue sources

Each via the standard source-adder recipe (probe → fixture → parser →
registry → parser test → docs), with the `window_days` opt-in and
indoor/outdoor default decided per source. A probe that finds no structured
feed means REJECT (headless was struck — see the decision above), same as
Phase 2.

**Ordering (2026-07-07 review): borough-coverage gap is the explicit
tiebreaker, ahead of platform-family learning.** Seven of eleven live sources
are Brooklyn venues; Queens, the Bronx, and Staten Island lean almost entirely
on the permit registry + nycgovparks. So Bronx Zoo, Queens Museum, NYPL/QPL,
SI Children's Museum come first — which conveniently still covers one of each
platform family. Also: every new source is a permanent maintenance annuity
paid in scraper-decay risk, and with ~2,400 events already flowing from
nycgovparks alone, the marginal source is worth less than in Phase 2 — be
pickier than this list. Prerequisite before adding any: land the canonical
tag vocabulary (issue #44) so new sources don't widen the fragmentation.

- **WCS (zoos + aquarium):** Bronx Zoo, Prospect Park Zoo, Central Park Zoo,
  NY Aquarium. Likely share one CMS — probe Bronx Zoo first; if the others
  share the shape, copy-adapt (same pattern as the three Tribe Events
  sources). Default outdoor (aquarium = mixed).
- **Museums:** Intrepid, Brooklyn Museum, Queens Museum, Staten Island
  Children's Museum, New York Hall of Science. Default indoor (NYSCI has a big
  outdoor science playground → mixed).
- **Gardens:** Brooklyn Botanic Garden. Default outdoor (conservatory → mixed).
- **Libraries:** NYPL, Queens Public Library — completes the three NYC library
  systems alongside BPL. Default indoor.
- **Parks programming:** City Parks Foundation / SummerStage Kids, Hudson
  River Park. Default outdoor; these are strong weather-enrichment candidates.

Probe order suggestion: do one of each platform family first to learn the
shape (WCS, a museum, a library system, a parks org), then fan out.

---

## Workstream C — Tech debt — **DONE**

- **#4 — FTS5 VACUUM footgun** — closed (documented in CLAUDE.md "DB
  migrations"; guarded procedurally by the `db-maintenance` skill).
- **#5 — split consent password from the master bearer** — closed
  (`MCP_CONSENT_PASSWORD`, see CLAUDE.md "OAuth model").
- **#6 — efficiency / hygiene grab-bag** — closed.

---

## Suggested sequencing

1. ~~Tech debt #4–#6 + caching-layer scaffolding~~ — done.
2. ~~A1 geocode + neighborhood~~ — done.
3. **Ingest observability** (issue #65: `ingest_runs` table + yield-drift
   alerting) — pulled ahead of everything else by the 2026-07-07 review; it
   protects all subsequent work and feeds the dashboard.
4. **A2 indoor/outdoor** — cheap; needed to scope weather.
5. **A3 weather** — depends on A1 + A2.
6. **New sources (Workstream B)** — borough-gap ordering (see above), tag
   vocabulary (#44) landed first, no headless.

## Open decisions to settle before building

- ~~Headless image~~ **SETTLED 2026-07-07: headless STRUCK entirely** (see the
  decision section at the top). The ingest-container split proceeds on its own
  merits (issue #68).
- ~~Weather: when to compute~~ **SETTLED: cached-with-TTL**, keyed by
  `neighborhood` (not per-event/per-venue coords) — see A3 above.
- ~~Geocoder~~ **SETTLED: US Census only** (Census tract → NTA crosswalk; no
  Nominatim fallback needed in practice).
- ~~Cache storage~~ **SETTLED: a `geocode_cache` table in `events.db`** (no TTL).
