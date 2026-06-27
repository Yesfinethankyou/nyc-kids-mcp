# Session Handoff

## What was done (most recent first)

### Session: Neighborhood coding + geocoding (branch `claude/neighborhood-event-coding-wkb2dh`)

Implemented Phase 3 A1's neighborhood + geocoding half (feat-006 + feat-009
partial). Neighborhoods are now populated by a **second nightly pass**, not by
sources.

- [x] **`enrich.py` second pass** — runs at the tail of `ingest.main` (guarded;
      `ENRICH=0` skips). Resolution ladder, first hit wins, only for rows with
      `neighborhood IS NULL`:
      1. fixed-venue source constant (`SOURCE_NEIGHBORHOOD`)
      2. enumerable multi-site (`VENUE_NEIGHBORHOOD`, NY Transit's 2 sites)
      3. open-data park table (`park_neighborhoods.json`, ~91% of permit rows)
      4. reverse-geocode existing lat/lng → NTA
      5. forward-geocode `"venue, city, NY"` → lat/lng (backfilled) + NTA
- [x] **`geocode.py`** — US Census geocoder client (forward + reverse, no key).
      Tract GEOID → NTA via the committed `tract_to_nta.json` crosswalk.
- [x] **`geocode_cache` table** in `events.db` (no TTL; negatives cached too) —
      a venue is geocoded at most once ever, so the nightly re-run is cheap even
      though the upsert nulls `neighborhood` each ingest.
- [x] **`sources/_neighborhoods.py`** — the static tables + `static_neighborhood()`
      + `nta_for_tract()`. Sibling to `_filters.py`.
- [x] **Open-data tables + build scripts** — `build_tract_nta.py` (Socrata
      `hm78-6dwm`, 2327 tracts), `build_park_neighborhoods.py` (Parks Properties
      `enfh-gkve` + Census batch/centroid, 1909 park keys), and
      `build_library_neighborhoods.py` (NYC FacDB `ji82-xba5` + Census, 221
      keys). Shared Census primitives in `scripts/_census.py`. JSON committed
      under `src/nyc_events/data/`.
- [x] **Library table** — `library_neighborhoods.json`, keyed
      `"<borough>|<library-core>"` (`library_core()` strips generic
      library/branch tokens). Codes **all 15** BPL feed branches; gated on a
      `library` token so a park can't borrow a library entry; borough-keyed to
      future-proof QPL/NYPL. `static_neighborhood()` now takes `borough`.
- [x] **Egress documented** — ingest already needs outbound HTTPS (all sources
      fetch external hosts); enrich adds `geocoding.geo.census.gov`. No egress
      allowlist in compose. Debt noted: if egress is ever hardened, add the host.
- [x] **BAM** added to `SOURCES-BACKLOG.md` as a CANDIDATE to probe (BAMkids;
      likely Tessitura — verify).
- [x] **Server** — `neighborhood` added to `_event_summary`; `search_events`
      gains a `neighborhood` filter (case-insensitive substring) wired through
      `db.search`.
- [x] **Tests** — `test_neighborhoods.py`, `test_enrich.py` (injected geocoders,
      no network), `test_event_projection.py`, plus geocode-cache + neighborhood-
      filter cases in `test_db.py`. **436 passed, ruff clean.**
- [x] **Docs** — CLAUDE.md (new "Neighborhood coding" section + Commands/Layout/
      Test/roadmap), PHASE-3-PLAN.md (A1 marked done, geocoder/cache decisions
      settled), README (ingest cron + permit-limits + tools), progress.md.

### Session: Filter-review pass (PR #21, open)

Implemented every decision from `FILTER-REVIEW.md` — the cross-source
kid-relevance filters had drifted between six hand-maintained copies.

- [x] **obs. 1 — drop alcohol-tasting terms** everywhere: `cocktail`,
      `whiskey`/`whisky`, `sake`, `brewery`, `distillery`, `wine tasting`,
      `beer tasting`, `happy hour`. Alcohol at a venue isn't itself an
      adult-only signal; these dropped legit family events. Industry City now
      keeps the gourmet-tour + sake-class rows.
- [x] **obs. 1 leftover + obs. 2 — shared `src/nyc_events/sources/_filters.py`:**
      `normalize()` (collapse hyphens/whitespace so one spelling matches all
      variants), `contains_any()`, and the canonical sets `ADULT_BLOCKLIST`
      (title or body), `ADULT_TITLE_BLOCKLIST` (drag show/brunch — title only),
      `MEMBERS_ONLY`. The six editorial sources import these; venue extras stay
      local (`gala`/`qc ny` for Governors Island, `Nightlife`/`late night` for
      Industry City).
- [x] **obs. 3 — Green-Wood dead blocklist removed.** The soft
      `_BLOCKLIST_KEYWORDS` was unreachable (allowlist short-circuits first,
      default is a conservative drop). `adults only` moved into the shared
      hard-exclude so it actually overrides the allowlist.
- [x] **obs. 4 — word-boundary tag matching** across all keyword-tagging
      sources: `re.search(r"\b" + kw)`, so `art`≠start, `tree`≠street,
      `hill`≠Churchill, `walk`≠boardwalk, `sing`≠crossing, `moth`≠mother,
      `bus`≠business — prefixes (`puppet`→`puppets`) still match. Non-gating.
- [x] **`drag show`/`drag brunch` made title-only** (`ADULT_TITLE_BLOCKLIST`)
      so a family event whose body merely mentions an adjacent drag show is kept.
- [x] **Docs reconciled:** `FILTER-REVIEW.md` (all obs. marked resolved +
      per-source detail), `CLAUDE.md` (layout + hygiene section), and
      `SOURCES-BACKLOG.md` (tech-debt marked done).
- [x] **PR-workflow hook:** new PreToolUse hook
      `.claude/hooks/require-handoff-update.sh` (matcher
      `mcp__github__create_pull_request`) blocks PR creation unless
      `session-handoff.md` was updated for the branch (dirty / changed vs
      `origin/main` / in the latest commit). Fail-open on git errors. Documented
      in CLAUDE.md's new "PR workflow" section.

### Session: Issues #4 / #5 / #6 (merged in PR #19)

- [x] **Issue #4 — FTS5 VACUUM footgun (doc fix):** CLAUDE.md "DB migrations"
      section now warns never to run `VACUUM` on `events.db` without
      immediately rebuilding the FTS5 index. The `events` table has a TEXT
      primary key, so SQLite may renumber implicit rowids on VACUUM and silently
      desynchronize the external-content FTS5 index.
- [x] **Issue #5 — Split consent password from master bearer:** Added optional
      `MCP_CONSENT_PASSWORD` env var. `/authorize` POST checks it first, falling
      back to `MCP_AUTH_TOKEN`. With it set, the browser consent form never
      touches the master bearer; the two credentials rotate independently.
      `.env.example` and CLAUDE.md updated.
- [x] **Issue #6 — Hygiene grab-bag:** OAuth DB churn reduced (5-min in-memory
      token cache in `BearerAuthMiddleware`); rate-limiter buckets now evict
      when empty; tool args clamped (`limit ≤ 50`, `days_ahead ≤ 365`);
      `UTC = UTC` no-ops removed; `import json` moved to module level;
      Green-Wood `_strip_html` now uses `html.unescape()`.

## Current state

Suite: **439 passed**, ruff: **clean**. Neighborhood-coding work on
`claude/neighborhood-event-coding-wkb2dh`. Live smoke test of the real Census
path verified: Prospect Park→"Prospect Park" (park table), Domino→"Williamsburg"
(constant), NY Transit Museum→"Brooklyn Heights" (tier 2), Sunset Park
Library→"Sunset Park (West)" (library table), a Manhattan street
address→"Midtown-Times Square" with lat/lng backfilled (forward geocode).
Earlier filter-review pass is **PR #21** on `claude/laughing-planck-xaar2v`.

## Decisions made

- **Alcohol ≠ adult-only.** Alcohol-tasting terms removed from all blocklists;
  explicit `21+`/`adults only`/`no children`/`burlesque`/`drag` still gate.
- **Shared `_filters.py`, per-source extras stay local.** Hoist only the
  canonical adult sets + the normalizer; the inclusion *strategy* and
  venue-specific terms remain in each source.
- **`drag show`/`drag brunch` are title-only**; the core adult terms match
  title or body.
- **Green-Wood soft blocklist was dead code** — removed, adult terms promoted
  to the hard-exclude.
- **Handoff-before-PR is enforced by a hook**, not just convention — PR creation
  is blocked until `session-handoff.md` is updated for the branch.

## Blockers / risks

- **`guard-commit` hook** is active: any `git add` whose command text contains
  `.env`, `.venv`, or `data/*.db` is blocked. Run `pytest`/`ruff` (which use
  `.venv/bin/...`) in a *separate* Bash call from `git add`/`git commit`, or the
  hook trips on the literal `.venv` in the command string.
- **OAuth token cache** means a revoked token (row deleted from `oauth.db`)
  stays valid for up to 5 min in a running server.
- **Ingest egress (debt).** The enrich pass needs `geocoding.geo.census.gov`
  reachable at ingest time (ingest already needs outbound HTTPS for all
  sources). The repo has no egress allowlist; if the deployment ever adds one,
  add that host or neighborhood coding silently stops (guarded — no crash).

## Next session startup

1. Read `CLAUDE.md` (project guide — hard-won quirks, security baseline; the
   new "Neighborhood coding" section).
2. Read `progress.md` for current feature state.
3. Run `pytest tests/ -q` + `ruff check` — suite should be green (439).

## Recommended next steps

- **Distance-from-home / `near_me`** finishes Phase 3 A1 — the coords the
  enrich pass backfills now exist. Needs a home-location config (env vs row —
  still open in PHASE-3-PLAN.md).
- On first production ingest after deploy, the enrich pass will geocode all
  unmapped/freeform venues once (then cache). Spot-check `list_sources` /
  a few `get_event_detail` calls to confirm neighborhoods look sane.
- Optional follow-up for fuller coverage: the ~9% of permit parks whose names
  don't match the open-data table (BPL branches are now covered by the library
  table).
- **BAM** is queued in `SOURCES-BACKLOG.md` (CANDIDATE) — probe with
  `source-verifier` (likely Tessitura) before building.
- Merge **PR #21** (filter-review pass) — separate branch.
