# Phase 3 plan

Working plan for Phase 3. Phase 2 shipped the editorial-source set and cleared
its buildable backlog (see `SOURCES-BACKLOG.md`). Phase 3 makes events
**location-aware**, **expands venue coverage**, and pays down deferred
tech debt.

**Explicitly out of scope for Phase 3:** AI/LLM enrichment (age inference,
indoor/outdoor classification, cross-source dedup, semantic search). Revisit
in a later phase. Indoor/outdoor here is heuristic, not model-derived (see A2).

## Definition of done

- Geocoding + neighborhood backfilled for all rows that can be located;
  distance-from-home available as a search filter.
- Weather attached to upcoming **outdoor** events inside the forecast window.
- Indoor/outdoor flag on every event (heuristic + per-source default).
- The Phase-3 venue list below is each BUILT or REJECTED (source-adder recipe).
- Deferred issues #4, #5, #6 closed.
- Enrichment runs as a second nightly pass with a caching layer; source
  `fetch()` stays as simple as it is today.

---

## Decision: adopt a headless browser (Playwright) as a *fallback* render tool

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

**Recommendation:** adopt it as (a) opt-in per source, (b) a sync `_render`
helper, (c) a separate ingest image. Then headless is weight on the nightly
job, not complexity smeared across the app. **Open decision:** separate ingest
image (recommended) vs. baking Chromium into the single image.

---

## Workstream A — Location enrichment

Dependency chain: **geocode → (neighborhood, weather)**, and
**indoor/outdoor → weather relevance**. Build A1 first.

### A1. Geocode + neighborhood — **DONE** (neighborhood + coords), `near_me` pending

Shipped as the `enrich.py` second pass. See CLAUDE.md "Neighborhood coding" for
the as-built detail. Resolved the open decisions below: **geocoder = US Census**
(no key; tract GEOID → NTA via the committed `tract_to_nta.json` crosswalk —
Nominatim wasn't needed); **cache = a `geocode_cache` table in `events.db`**
(no TTL). Neighborhood now resolves through a 5-tier ladder (fixed-venue
constant → enumerable site → open-data park table → reverse-geocode → forward-
geocode), and `lat`/`lng` are backfilled as a side effect. Surfaced in the
`search_events` summary + a `neighborhood` substring filter. **Still TODO:** the
`near_me` / sort-by-distance affordance + a home-location config (the coords it
depends on now exist).

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
- New search affordance: `near_me` / sort-by-distance once a home location is
  configured (env or a tiny config row).

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
- Source: **NWS `api.weather.gov`** (free, no key). Needs coords (depends on
  A1); only meaningful for **outdoor** events (depends on A2) within the
  forecast window (~7 days).
- **Open decision — when to compute:**
  - *Nightly* (simple, ≤24h stale): enrich at the second pass; store forecast
    on the row.
  - *Read-time* (fresh, but adds latency + an external dependency to the tool
    hot path).
  - *Recommended:* cache forecasts by grid point with a short TTL (a few
    hours), refresh lazily — fresh enough for weekend planning, off the hot
    path, and NWS gridpoint lookups are coarse so the cache hit-rate is high.
- Tool output: attach a compact forecast (temp range + precip/condition) to
  applicable events; Claude can warn "rain likely Saturday."

### Architecture: second nightly pass + caching layer
- Keep source `fetch()` dumb. Add an **enrichment step** that runs after
  ingest (same nightly job, second phase): geocode missing coords → attach
  neighborhood → fetch weather for upcoming outdoor events.
- **Caching layer:** a geocode cache (stable, no TTL — key = lookup string)
  and a weather cache (short TTL — key = NWS gridpoint). Decision: new tables
  in `events.db` vs. a third SQLite file. Leaning new tables in `events.db`
  (it's event-derived data), kept clearly namespaced.

---

## Workstream B — New venue sources

Each via the standard source-adder recipe (probe → fixture → parser →
registry → parser test → docs), with the `window_days` opt-in and
indoor/outdoor default decided per source. Headless `_render` is the fallback
only if a probe finds no structured feed.

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

## Workstream C — Tech debt (server-touching; bundle together)

Best done in one server-touching pass — the A3/`near_me` read-path work will
already be in `server.py`, so fold these in then:
- **#4 — FTS5 VACUUM footgun** (operational/doc).
- **#5 — split consent password from the master bearer** (the one-env-var,
  two-roles coupling in the OAuth model).
- **#6 — efficiency / hygiene grab-bag.**

---

## Suggested sequencing

1. **Tech debt #4–#6 + caching-layer scaffolding** — server-touching; do
   together while we're in `server.py`/`db.py`.
2. **A1 geocode + neighborhood** — prerequisite for weather; immediately
   useful (distance/near-me) on the existing ~1,150 events.
3. **A2 indoor/outdoor** — cheap; needed to scope weather.
4. **A3 weather** — depends on A1 + A2.
5. **New sources (Workstream B)** — cheap REST/JSON-LD first; adopt the
   headless ingest image only when a probed source actually needs it.

## Open decisions to settle before building

- Headless image: separate ingest image (recommended) vs. single image.
- Weather: nightly vs. read-time vs. cached-with-TTL (recommended).
- ~~Geocoder~~ **SETTLED: US Census only** (Census tract → NTA crosswalk; no
  Nominatim fallback needed in practice).
- ~~Cache storage~~ **SETTLED: a `geocode_cache` table in `events.db`** (no TTL).
- Home location: env var vs. a config row (for distance-from-home). Still open —
  needed for the remaining `near_me` piece of A1.
