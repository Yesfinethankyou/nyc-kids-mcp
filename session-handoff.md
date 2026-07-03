# Session Handoff

## What was done (most recent first)

### Session: DB init/connect split + dead-dep cull, issues #28 & #29 (branch `claude/github-issues-28-29-7ks73y`)

Two architecture-review findings from 2026-07-02.

- [x] **#28 — schema DDL off the read path.** Split `db._open()` into
      `db._connect()` (plain: WAL + `row_factory` + FK pragma, **no DDL**) and
      `db.init_events()` / `db.init_oauth()` (schema `executescript` +
      `_migrate_*`). `connect_events` / `connect_oauth` are now plain opens, so
      a per-request `search_events` connection no longer re-runs `CREATE TABLE`/
      `ALTER` and takes a write lock contending with the nightly ingest. `init_*`
      is called once at each entry point: `server.build_app()`, `ingest.main`,
      `enrich.main`, `seed_fake.main`. Test fixtures that create a fresh DB now
      call `init_*` first; the two migration tests (`test_migration_adds_
      missing_since_column`, `test_oauth_migration_adds_expires_at_column`) call
      `init_*` explicitly since that's where migrations now live.
- [x] **#29 — removed unused deps** `feedparser`, `icalendar`,
      `python-dateutil` from `pyproject.toml` (imported nowhere in
      `src`/`tests`/`scripts`; Phase-1 RSS/iCal anticipation that never
      materialized). Re-add `icalendar` if an iCal source shows up in Phase 3.
- [x] **Docs** — CLAUDE.md "DB migrations" section rewritten to describe the
      init/connect split.
- [x] **Verified** — full suite **455 passed, ruff clean**; runtime smoke test
      confirms `build_app()` creates + migrates both DBs and `connect_*` opens
      them plainly.

### Session: neighborhood persistence, issue #27 (branch `claude/architecture-design-review-8r5735`, same session as #26/#25 below)

Fixed the wipe-and-restore fragility: the nightly upsert used to null every
row's `neighborhood` and rely on a best-effort enrich pass to restore it, so
one failed pass left the whole catalog without neighborhoods (and
`search_events(neighborhood=...)` returning nothing) for 24h, silently.
Implemented issue #27's option 1 + the option-2 exit code.

- [x] **Upsert preserves enrichment** (`db.upsert_events`): `neighborhood`/
      `lat`/`lng` now use CASE expressions — a source-provided value wins;
      otherwise the enriched value is kept; and the coding resets to NULL
      exactly when the row's **venue or borough changed** this ingest
      (null-safe `IS NOT`), so stale coding re-resolves the same night.
      That last clause handles the staleness objection to plain COALESCE
      (an event moved to a new venue no longer keeps the old venue's label).
- [x] **`enrich --recode-all`** — new CLI flag / `run(recode_all=True)`:
      re-resolves every row (not just `neighborhood IS NULL`); needed now
      that static-table corrections no longer propagate via the nightly
      wipe. Conservative: a row whose re-resolution fails keeps its old
      label (recode only adds/updates, never removes). `allow_abbrev=False`
      so `--recode` fails loudly instead of silently matching (caught live
      during verification).
- [x] **Ingest exit code 3** when the enrich pass raises (sources still
      commit first; source failures keep exit 2, which takes precedence) —
      the DSM cron can now alert instead of the failure landing in stderr
      of a 0-exit run.
- [x] **Tests** — 4 new upsert-persistence cases in `test_db.py` (preserve
      on re-ingest / source wins / venue change resets / borough change
      resets), 2 new recode cases in `test_enrich.py` (reprocesses coded
      rows; keeps label on failed resolution). **455 passed, ruff clean.**
- [x] **Runtime-verified via the real CLIs** on a seeded temp DB: nightly
      enrich coded only the NULL row (offline park tier), second run 0/0,
      `--recode-all` re-resolved all 6 rows through the live Census
      geocoder (5 misses kept their labels, 1 stale label recoded), second
      recode served entirely from the negative cache (0 HTTP requests),
      re-seed (UPDATE path) blanked nothing.
- [x] **Docs** — CLAUDE.md: Commands (+`--recode-all`), the "Persistence"
      paragraph replaces "Why re-running nightly is cheap", ingest exit
      codes (0/2/3), egress-debt note updated (existing rows keep labels;
      blocked-geocoder misses are cached as negatives — check
      `geocode_cache` when debugging).

### Session: server.py split, issue #26 (branch `claude/architecture-design-review-8r5735`, same session as #25 below)

Split the 926-line `server.py` on churn vs consequence, per issue #26. Pure
move — no handler/middleware logic changed (one attempted "improvement" to
the middleware style was caught and reverted mid-session; the security
surface ships byte-equivalent logic).

- [x] **`auth.py`** (new) — the "do not regress" surface: rate limiter +
      buckets, OAuth token cache, `BearerAuthMiddleware`, redirect-URI
      allowlist, discovery endpoints, `/register`, `/authorize` GET/POST,
      `/token`, consent HTML + security headers. Module docstring carries the
      single-process warning (issue #30 item 1); CLAUDE.md security baseline
      gained a matching **single-worker only** bullet.
- [x] **`tools.py`** (new) — the MCP surface: `FastMCP` instance, all seven
      tools, `_event_summary`/`_event_detail`, `_weekend_window`,
      `_normalize_borough`, `_local_date`, `_venue_map_url`,
      `_possibly_cancelled`.
- [x] **`config.py`** (new, issue #30 item 2) — env-derived settings read
      once: `DB_PATH` (was read in **four** places: server/ingest/enrich/
      seed_fake — all now `config.DB_PATH`), `OAUTH_DB_PATH`, `PORT`,
      `FORWARDED_ALLOW_IPS`, `OAUTH_TOKEN_TTL_DAYS`, redirect allowlist.
      Consumers use attribute access so tests monkeypatch `config.X`.
      Credentials deliberately stay call-time env reads (master token never
      sits in an importable module attribute).
- [x] **`server.py`** now 97 lines: `build_app()` + `main()` only.
- [x] **Tests repointed** (import/monkeypatch targets only, no assertion
      changes): `test_security_fixes` → `auth`, `test_search_tools` →
      `tools` + `config.DB_PATH`, `test_event_projection` /
      `test_weekend_window` / `test_missing_detection` → `tools`.
- [x] **Runtime-verified end-to-end** (booted the real server on a temp DB):
      browser probe 200 / POST 401, discovery JSON, consent page + all
      security headers, evil-redirect 400, full OAuth flow (register →
      consent with separate consent-pw AND master fallback → PKCE exchange →
      issued bearer accepted), auth-code single-use, MCP protocol round trip
      (initialize → tools/list shows all 7 → tools/call returns seeded rows),
      rate limiter 429s at request 6 with Retry-After, GET /token downgrade
      guard 400s. **449 passed, ruff clean.**
- [x] **Docs** — CLAUDE.md Layout (four module entries replace the server.py
      line; the ">600 lines → split" paragraph replaced by "never blend them
      back"); security baseline gained the single-worker bullet.

### Session: Tribe source consolidation, issue #25 (branch `claude/architecture-design-review-8r5735`)

Architecture-review session: filed issues #25–#30 from a full design review,
then implemented **#25** — the four WordPress / The Events Calendar (Tribe)
sources were ~150-line copies of each other and had already drifted.

- [x] **New `src/nyc_events/sources/_tribe.py`** — everything that is a
      property of the *plugin*, not the venue: `TribeEventsSource` (the
      fetch/pagination loop + curl_cffi Chrome-impersonation page fetch),
      `parse_row`/`RowParts` (the common row skeleton: kid-relevance gate,
      title, UTC dates, per-occurrence external_id, excerpt-preferred
      description + 2000-char trim, raw_payload), and the canonical
      `strip_html` / `parse_utc_dt` / `parse_cost` / `category_names`.
- [x] **Four sources rewritten as subclasses** — `greenwood_cemetery`,
      `prospect_park`, `industry_city`, `ny_transit_museum` now keep only
      venue-specific logic: filter strategy, tag rules, venue/borough/price
      mapping (incl. NY Transit's venue-object mapping + "Included with
      Museum admission"→PAID override, Industry City's always-UNKNOWN price).
      Each keeps a module-level `_parse_row` (assigned into the class via
      `staticmethod`) plus `_strip_html`/`_parse_utc_dt`/`_parse_cost` aliases
      so the parser tests exercise them unchanged. **Net −634 lines.**
- [x] **Drift fixed: entity decoding unified on `html.unescape`.**
      Prospect Park / Industry City / NY Transit hand-replaced a fixed handful
      of entities (Green-Wood already used unescape). Behavior change:
      `&#8217;` now decodes to the real `’` (U+2019), not a normalized ASCII
      `'` — three test assertions updated to the faithful decode. Event ids
      are unaffected (all four sources have per-occurrence external_ids).
- [x] **`window_days` double-duty collapsed** (issue #30 item 3, Tribe
      sources only): one attribute, set once in the base `__init__`; the
      `self._window_days`/`self.window_days` duplication is gone. Base opts
      into missing-detection by default (all Tribe sources are full-window).
- [x] **Smoke-tested beyond the suite:** all four classes instantiate via the
      `ENABLED_SOURCES` no-arg path, and the shared `fetch()` loop yields
      correct events end-to-end against stubbed pages from the fixtures.
- [x] **Docs** — CLAUDE.md Layout (new `_tribe.py` entry: subclass, never
      copy-adapt), `.claude/agents/source-adder.md` Tribe fast-path now
      points at `TribeEventsSource`. **449 passed, ruff clean.**

### Session: MCP tool filters + facet discovery (branch `claude/mcp-tools-review-6unjn8`)

Reviewed the MCP tool surface and implemented three of the suggested gaps. No
schema/migration changes — all additive filtering over the existing `db.search`.

- [x] **`exclude_low_confidence` filter** — new bool on `db.search` (`description
      IS NOT NULL OR url IS NOT NULL`) and exposed on all three listing tools
      (`search_events`, `events_this_weekend`, `events_on_date`). Drops permit-
      style rows for the "only curated, attendable events" path; mirrors the
      existing `low_confidence` output flag. Fixes browse tools being flooded by
      the ~700-row permit source.
- [x] **Arbitrary date window on `search_events`** — `start_date`/`end_date`
      (YYYY-MM-DD, NYC local). `start_date` defaults the window start to that
      date instead of now; `end_date` omitted → `start + days_ahead` (same
      precise-instant semantics as the existing now-window). End-before-start
      and bad formats raise `ValueError`. Shared `_local_date()` helper (also
      now used by `events_on_date`).
- [x] **`source` filter on `search_events`** — restrict to one source id.
- [x] **`list_facets()` new tool** — distinct in-catalog `boroughs`,
      `neighborhoods`, `tags`, `sources` so a caller can discover valid filter
      values. `db.list_facets()`; tags unpacked from per-row JSON in Python (no
      json1 dependency).
- [x] **`search_events` default `limit` 10 → 15** (others stay 10; all cap 50).
- [x] **Tests** — `test_db.py` (source filter, exclude_low_confidence, two
      `list_facets` cases); new `test_search_tools.py` (date-range window math,
      end-before-start guard, bad-format guard, exclude_low_confidence + facets
      through the tool layer, via monkeypatched `server.DB_PATH`). **449 passed,
      ruff clean.**
- [x] **Docs** — CLAUDE.md "Tool output shape" (filters + list_facets + new
      default limit), README tool table (7 tools now).
- [x] **Backlog additions** (`SOURCES-BACKLOG.md`, unprobed CANDIDATEs) — three
      Manhattan art museums (The Met, MoMA, The Whitney) under a shared "NYC art
      museums" note (curated adult-skewing → family-strand gate; the Met is a
      two-site `VENUE_NEIGHBORHOOD` case), and **The Skint** (theskint.com) — a
      citywide WordPress RSS blog flagged with the two probe blockers that decide
      buildability: digest-vs-per-event item granularity and low kid-yield.

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
- [x] **Library systems backlog** — added Queens Public Library, NYPL, Bronx,
      and Staten Island as CANDIDATE items, with a system-map note: NYC has 3
      systems (BPL built, QPL, NYPL=Manhattan+Bronx+SI), so Bronx/SI are NYPL
      borough slices. Neighborhood coding already covers all of them (the
      library table is NYC-wide + borough-keyed) — a future source just needs to
      set each event's branch borough.
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

Suite: **455 passed**, ruff: **clean**. Issues #25 (Tribe consolidation),
#26 (server split), and #27 (neighborhood persistence) implemented on
`claude/architecture-design-review-8r5735`. Architecture-review issues
**#28** (db.init/connect split) and **#29** (unused deps) now implemented on
`claude/github-issues-28-29-7ks73y`; **#30** is fully absorbed (items 1+2 with
#26, item 3 with #25).

**Deploy note for #27:** after this lands, corrections to the static
neighborhood tables need a one-off `docker exec … python -m nyc_events.enrich
--recode-all` to reach already-coded rows — the nightly wipe that used to
propagate them implicitly is gone (that wipe was the bug).

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
