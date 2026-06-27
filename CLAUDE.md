# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Project guide for future Claude Code sessions. Captures hard-won knowledge
that isn't obvious from reading the code. See README.md for end-user setup.

## What this is

Single-user MCP server that ingests NYC family-friendly events into SQLite + FTS5
and exposes them as MCP tools over streamable HTTP. Designed to be reached by
claude.ai web/mobile via a Tailscale Funnel hostname, gated by an OAuth 2.1 +
PKCE shim that uses `MCP_AUTH_TOKEN` as the consent password. Phase 1 = NYC
permit data only (one source); Phase 2 = editorial scrapers.

## Commands

```bash
.venv/bin/python -m nyc_events.server           # run HTTP server (needs MCP_AUTH_TOKEN)
.venv/bin/python -m nyc_events.ingest           # one-shot ingest (runs the enrich pass at the end)
.venv/bin/python -m nyc_events.enrich           # second pass alone: code neighborhoods + backfill lat/lng (network)
.venv/bin/python -m nyc_events.seed_fake        # populate fake events for connector smoke-testing
.venv/bin/python scripts/build_tract_nta.py     # rebuild data/tract_to_nta.json (one-shot, from NYC open data)
.venv/bin/python scripts/build_park_neighborhoods.py     # rebuild data/park_neighborhoods.json (one-shot)
.venv/bin/python scripts/build_library_neighborhoods.py  # rebuild data/library_neighborhoods.json (one-shot)
.venv/bin/python -m pytest tests/ -q            # full test suite (should always be green)
.venv/bin/python -m pytest tests/test_security_fixes.py::test_rate_limiter_is_per_ip -q  # single test
.venv/bin/ruff check                            # lint
```

If a change breaks tests, fix the change — don't loosen the tests.

## PR workflow

**Always update `session-handoff.md` to reflect the session's work and commit
it BEFORE opening a PR.** This is enforced: a PreToolUse hook
(`.claude/hooks/require-handoff-update.sh`, matcher
`mcp__github__create_pull_request`) blocks PR creation unless the handoff has
been touched for the current branch (dirty, changed vs `origin/main`, or in the
latest commit). Update the handoff first and the PR proceeds.

## Layout

- `src/nyc_events/models.py` — `Event` + `Borough` / `Price` enums + `compute_id()`
- `src/nyc_events/db.py` — split SQLite stores (events + oauth) with idempotent migrations
- `src/nyc_events/server.py` — FastMCP + OAuth shim + bearer middleware + MCP tools
- `src/nyc_events/oauth.py` — auth-code issue/consume + PKCE verification
- `src/nyc_events/ingest.py` — CLI loop over `ENABLED_SOURCES`; runs `enrich` at the end
- `src/nyc_events/enrich.py` — second-pass location enrichment (neighborhood coding + lat/lng backfill)
- `src/nyc_events/geocode.py` — US Census geocoder client (forward + reverse; no API key)
- `src/nyc_events/data/` — committed open-data tables: `tract_to_nta.json`
  (census tract → NTA neighborhood), `park_neighborhoods.json` (park name → NTA),
  `library_neighborhoods.json` (borough+library-core → NTA). Built by
  `scripts/build_*.py`; loaded as package data.
- `src/nyc_events/sources/base.py` — `Source` ABC; each source is one file in the same dir
- `src/nyc_events/sources/_filters.py` — shared kid-relevance helpers:
  `normalize()` (collapse hyphens/whitespace), `contains_any()`, and the
  canonical adult sets: `ADULT_BLOCKLIST` (match title or body),
  `ADULT_TITLE_BLOCKLIST` (drag show/brunch — title only), `MEMBERS_ONLY`.
  Per-source extras + the inclusion *strategy* stay in each source.
- `src/nyc_events/sources/_neighborhoods.py` — neighborhood coding tables:
  `SOURCE_NEIGHBORHOOD` (fixed-venue sources), `VENUE_NEIGHBORHOOD` (enumerable
  multi-site), the park-table + tract-crosswalk loaders, and `static_neighborhood()`.
- `src/nyc_events/sources/__init__.py` — `ENABLED_SOURCES` registry
- `scripts/build_tract_nta.py` / `build_park_neighborhoods.py` /
  `build_library_neighborhoods.py` — one-shot data-prep that regenerates the
  `data/*.json` tables from NYC open data + Census (`scripts/_census.py` holds
  the shared batch/reverse geocoder primitives).
- `tests/fixtures/` — captured real upstream responses used in parser tests

`server.py` is a single big module. If it grows past ~600 lines, split the
OAuth handlers (`/authorize`, `/token`, `/register`, discovery) into their
own file before touching anything else.

## Test architecture

- `tests/test_db.py` — schema + migrations + upsert/search semantics
- `tests/test_security_fixes.py` — Checkpoint C bundle (rate limiter,
  redirect allowlist, consent headers, OAuth expiry). Direct unit calls,
  no HTTP layer.
- `tests/test_nyc_permitted_events_parse.py` — parser + `_clean_row` against
  real captured rows in `tests/fixtures/`.
- `tests/test_missing_detection.py` — possible-cancellation flagging
  (mark/clear semantics, circuit breaker, grace period, source opt-in).
- `tests/test_neighborhoods.py` — static neighborhood lookups (the three
  no-network tiers + the tract crosswalk against the shipped data tables).
- `tests/test_enrich.py` — enrichment resolution ladder with **injected**
  geocoders (the suite never hits the network): static tiers don't call out,
  network tiers call once then serve from `geocode_cache`, forward geocoding
  backfills lat/lng.
- `tests/test_event_projection.py` — `_event_summary`/`_event_detail` shape
  (neighborhood is now surfaced in list summaries, not just detail).
- New sources: add a fixture under `tests/fixtures/` from a real upstream
  response, then a `test_<source>_parse.py` that exercises the parser
  directly. Don't mock httpx — the parser takes a dict, not a response.

## claude.ai web connector quirks (will bite you again if you forget)

These have all cost us real time. Don't relearn:

1. **claude.ai treats the pasted URL as the MCP endpoint itself** — no `/mcp`
   suffix appended. We serve at root via `FastMCP(..., streamable_http_path="/")`.
   GET / and POST / both go to the MCP handler.
2. **claude.ai's UI does a browser-side fetch(url) before submitting the
   "Add connector" form.** The browser doesn't follow `WWW-Authenticate`
   discovery. If GET / returns 401, the UI says "URL is bad" and the
   server-side OAuth flow never starts. Our middleware returns **200 JSON**
   for unauthenticated GET / without an MCP session id so the probe passes;
   POST / still 401s correctly so the backend can negotiate OAuth.
3. **FastMCP auto-enables DNS-rebinding protection** when `settings.host` is
   loopback, which 421s every request from a Tailscale Funnel hostname.
   We disable it (`transport_security=TransportSecuritySettings(
   enable_dns_rebinding_protection=False)`) because bearer auth is the real
   gate, not Host header validation.
4. **claude.ai web requires OAuth** — there is no UI for a static bearer.
   Our shim is minimal: Dynamic Client Registration accepts anything,
   `/authorize` is a one-field form that uses `MCP_AUTH_TOKEN` as the
   consent password, `/token` issues opaque access tokens with TTL.
   Direct curl with the master token bypasses all this and still works
   for testing.
5. **`FORWARDED_ALLOW_IPS` MUST cover the Docker bridge gateway IP** —
   otherwise uvicorn silently ignores `X-Forwarded-Proto: https`, falls
   back to `http`, and the OAuth discovery JSON advertises `http://…`
   endpoints. claude.ai POSTs to `http://…/token`, Tailscale Funnel 302s
   to `https`, httpx (their MCP client) auto-follows the 302 and
   **downgrades POST→GET, dropping the request body**. Our `/token`
   handler then sees a malformed GET (form content-type, empty body,
   no params), can't exchange the code, the bearer is never issued, and
   every subsequent `POST /` arrives with no Authorization header. The
   symptom looks like a broken claude.ai client; the root cause is our
   config. `docker-compose.yml` sets `FORWARDED_ALLOW_IPS=*` because the
   host port is bound to `127.0.0.1`, so only Funnel/localhost can reach
   the container — wildcarding is safe under that bind constraint.

## OAuth model

- `MCP_AUTH_TOKEN` = master bearer AND fallback consent-page password.
- `MCP_CONSENT_PASSWORD` = optional separate consent-page password for `/authorize` POST.
  When set, the browser form accepts this instead of `MCP_AUTH_TOKEN`, so the master
  bearer is never typed into a browser. Falls back to `MCP_AUTH_TOKEN` when unset
  (original single-var behaviour). The two credentials can be rotated independently.
- `oauth_tokens` table = OAuth-issued access tokens. Lives in `data/oauth.db`,
  intentionally separate from `data/events.db` so wiping events during dev
  does not log claude.ai out.
- **Rotating `MCP_AUTH_TOKEN` does NOT revoke already-issued access tokens.**
  This asymmetry is intentional. To revoke a connector:
  `DELETE FROM oauth_tokens WHERE client_id = ?` on `data/oauth.db`.
- Issued tokens default to **90-day TTL** (`OAUTH_TOKEN_TTL_DAYS`). Legacy
  rows with NULL `expires_at` are grandfathered (still valid until manual delete).
- Redirect-URI allowlist gates `/authorize` GET and POST. Default:
  `https://claude.ai/api/mcp/auth_callback`, `http://localhost`, `http://127.0.0.1`.
  Override via `OAUTH_REDIRECT_URI_ALLOWLIST` env (comma-separated entries).
  Matching is by URL components (exact scheme + hostname, port if the entry
  pins one, path prefix if present), NOT string prefix — so
  `http://localhost.attacker.com` does not match the `http://localhost` entry.

## Stable ID semantics

`compute_id(source, external_id, ...)` returns a 16-char hex.
- Hashes `source|id:external_id` when `external_id` is present.
- Falls back to URL, then to `title|venue|date`.
- **`start_dt` is intentionally NOT in the hash.** When a source revises an
  event's time, the row updates in place rather than creating a new row +
  leaving the old one stale.

**Per-source override for recurring permits:** in `nyc_permitted_events`,
the upstream `event_id` is a *permit* id — one id can cover 31 recurring
occurrences. The source's `_parse_row` binds
`external_id = f"{permit_id}:{start.isoformat()}"` so each occurrence is its
own DB row. Apply the same pattern for any source where the upstream id
isn't per-occurrence.

## DB migrations

- Each `connect_*` runs an idempotent `_migrate_*` after schema creation.
- Migrations are `PRAGMA table_info` to read existing columns + `ALTER TABLE
  ADD COLUMN` if missing. No timestamp-based migration framework — keep it
  this simple unless we genuinely outgrow it.
- Two SQLite files: `data/events.db` (data) and `data/oauth.db` (tokens).
  They have separate schemas. Do not cross-reference.
- Precedents: `events.raw_payload TEXT`, `oauth_tokens.expires_at TEXT`.
- The `geocode_cache` table lives in `events.db` (it's event-derived). It's a
  plain `CREATE TABLE IF NOT EXISTS` in `EVENTS_SCHEMA`, not a `_migrate_*`
  column-add — a whole new table is idempotent on its own.
- **Never run `VACUUM` on `data/events.db` without immediately rebuilding the
  FTS index.** `events` has a TEXT primary key (no `INTEGER PRIMARY KEY` alias),
  so SQLite may renumber its implicit rowids on VACUUM. `events_fts` is an
  external-content FTS5 table keyed on those rowids; after a renumber the
  full-text index silently desynchronizes and returns wrong results. Fix with:
  `INSERT INTO events_fts(events_fts) VALUES('rebuild');`

## Source-data hygiene philosophy

The Phase-1 source (`tvpp-9vvx`) is a permit registry, not a curated event
listing. **Aggressive filtering is correct**, not over-engineering:
- Agency allowlist (e.g. `event_agency='Parks Department'`)
- `event_type` allowlist (drops sport-league permits, parades, religious events)
- Title regex blocklist (school identifiers `PS \d+ / I.S. \d+`, religious
  phrases, load-in/out, RC-plane hobbies, etc.)
- Kid-keyword tag required — events without any matched tag are noise from
  this source

The `_clean_row(row)` helper applies BEFORE field extraction AND BEFORE
`raw_payload` preservation. The preserved payload reflects the lightly-
cleaned input. Strict-raw semantics would require restructuring (`raw_payload
= json.dumps(row)` before `_clean_row` mutates it).

**Each editorial source carries its own kid-relevance filter** — strategies
vary by venue (allowlist-required, inclusive+blocklist, category-allowlist,
age-band). `FILTER-REVIEW.md` is the cross-source inventory: every inclusion
gate, the actual keyword/category lists, and the resolved inconsistencies. The
filter-consolidation pass is **done** (maintainer review, 2026-06): alcohol-
tasting terms dropped everywhere; the shared adult signals hoisted into
`sources/_filters.py` (`ADULT_BLOCKLIST` / `MEMBERS_ONLY` + a `normalize()` that
collapses hyphen/space variants); Green-Wood's dead soft-blocklist removed (its
adult terms promoted to the hard-exclude); and tag inference word-boundary-
matched so short keywords (`art`/`tree`/`hill`) stop hitting mid-word. When
adding a source, import the shared adult set from `_filters.py`, pick the
matching strategy, and keep venue-specific extras local — don't add a filter to
a curated kids feed (`mommy_poppins`, `bk_childrens_museum` have none by design).

## Tool output shape

Listing tools (`search_events`, `events_this_weekend`, `events_on_date`)
return the **summary** dict via `_event_summary(ev)`:
- Token-efficient — drops `external_id`, `end_local`, `lat`, `lng`,
  `age_min`, `age_max`, `source`. (`neighborhood` IS included — it's cheap and
  high-signal for "what's near X" questions; `search_events` also filters on it.)
- Truncates `description` to 200 chars.
- Default `limit=10`.

Drilling tools return the full record:
- `get_event_detail(event_id)` → `_event_detail(ev)` (all normalized fields,
  untruncated description, **no** raw_payload).
- `get_event_raw(event_id)` → original upstream JSON dict (or None).

All event dicts include:
- `event_id` (our stable internal id; pass back to drill tools)
- `venue_map_url` (Google Maps lookup synthesized from venue + borough; the
  best clickable when `url` is null, which is most permit rows)
- `low_confidence: bool` (true when `description IS NULL AND url IS NULL` —
  Claude should caveat these to the user)
- `possibly_cancelled: bool` (true when the event has been missing from its
  source's ingest for > 30h — see "Missing-event detection" below)

## Neighborhood coding (location enrichment)

Neighborhoods are populated by a **second pass** (`enrich.py`) that runs after
ingest, NOT by sources. Sources still yield `neighborhood=None`; keeping
`fetch()` dumb is deliberate (same split as missing-detection). `ingest.main`
calls `enrich.run` at the end, guarded so a geocoder hiccup can't fail the
ingest; `ENRICH=0` skips it for offline dev.

**Resolution ladder** (`enrich.resolve`, first hit wins, only for rows where
`neighborhood IS NULL`):
1. **Fixed-venue source** → `SOURCE_NEIGHBORHOOD` constant (Domino→Williamsburg,
   Industry City/BAT→Sunset Park, BCM→Crown Heights, Prospect Park, etc.).
2. **Enumerable multi-site** → `VENUE_NEIGHBORHOOD` (NY Transit Museum's two sites).
3. **Library branch** → `library_neighborhoods.json` (keyed by borough +
   library-core; codes all of BPL's branches).
4. **Park name** → `park_neighborhoods.json` (covers ~91% of permit rows).
5. **Row already has lat/lng** → reverse-geocode → NTA.
6. **Forward-geocode** `"venue, city, NY"` → lat/lng (backfilled) + NTA.
7. else `None` (the status quo — graceful).

Tiers 1–4 are pure/offline; tiers 5–6 hit the **US Census geocoder**
(`geocode.py`, no key) → 2020 census tract GEOID → NTA name via
`tract_to_nta.json`. Every network result, **including negatives**, is cached
in `geocode_cache` (no TTL; keyed `fwd:<venue|borough>` / `rev:<rounded
lat,lng>`), so a venue is geocoded at most once ever.

**Why re-running nightly is cheap:** the upsert nulls `neighborhood` every
ingest (it's `excluded.neighborhood`), so enrich reprocesses the whole table
each run — but tiers 1–3 are dict lookups and tiers 4–5 are `geocode_cache`
hits, so steady-state network calls ≈ only brand-new venues. The `UPDATE` fills
`lat`/`lng` via `COALESCE` (never clobbers a source-provided coord) and fires
the FTS `events_au` trigger, so neighborhood is searchable immediately.

**Gotchas that already cost time:**
- **Census uses USPS city, not borough.** Manhattan's city is `New York`, not
  `Manhattan` (`enrich._BOROUGH_CITY`). The park builder hit the same wall.
- **Multi-ZIP fields.** Parks Properties lists several ZIPs (`"11364, 11423"`);
  the batch CSV needs one — `build_park_neighborhoods.py` takes the first.
- **Big parks have no street number.** Cunningham/Prospect/Bronx Park geocode by
  multipolygon centroid (reverse), not address — the address pass alone left
  the highest-traffic permit parks `None`.
- **Label styles differ on purpose.** Tier-1/2 labels are colloquial
  (`Sunset Park`); tier 3–5 are official NTA names (`Crown Heights (North)`).
  The `search_events` neighborhood filter is a **case-insensitive substring**,
  and the curated labels are chosen to be substrings of the NTA names, so
  `neighborhood="Crown Heights"` unifies both.
- **Known `None` residue (acceptable):** ~9% of permit parks (name mismatches
  like `Randall's Island Park`) and freeform venues Census can't resolve.
  `None` is the status quo, so this only ever adds coverage. (BPL branches are
  now covered by the library table — they used to be the big gap.)

**Data tables** are built once, offline, and committed (not fetched at ingest):
`build_tract_nta.py` (Socrata `hm78-6dwm`), `build_park_neighborhoods.py`
(Parks Properties `enfh-gkve` + Census), and `build_library_neighborhoods.py`
(NYC FacDB `ji82-xba5` + Census). Re-run them to refresh; provenance is in each
script's docstring. The shared Census batch/reverse primitives live in
`scripts/_census.py`.

**Library branches** get their own table (`library_neighborhoods.json`), keyed
`"<borough>|<library-core>"` where `library_core()` strips the generic
`library`/`branch`/`info commons` tokens so the BPL feed's `Arlington Library`
keys the same as FacDB's `ARLINGTON LIBRARY`. Borough-keyed so a `Central
Library` in Brooklyn vs. Queens can't collide (future-proofs QPL/NYPL sources).
The lookup is gated on the venue containing a `library` token so a park like
`Sunset Park` can't borrow `Sunset Park Library`'s entry.

**Egress / outbound HTTPS:** the nightly ingest already needs outbound HTTPS —
all ~11 sources fetch external hosts — so the enrich pass adds no *new*
requirement, just new hosts: `geocoding.geo.census.gov` (runtime, tiers 4–5)
and, for the offline `build_*.py` scripts only, `data.cityofnewyork.us`. The
repo imposes no egress allowlist (`docker-compose.yml` has no network policy).
**Debt:** if the deployment is ever hardened to an egress allowlist, those hosts
must be added or the pass silently codes nothing (it's guarded, so ingest still
succeeds — you'd see `neighborhood` stop populating, not a crash).

## Missing-event detection (possible cancellations)

A future event that disappears from its source's feed is flagged, never
deleted. Four layers prevent a fetch blip from mass-flagging a source:

1. **Hard failures opt out** — a source whose `fetch()` raises is skipped
   entirely in `ingest.py`; its rows are never evaluated.
2. **Circuit breaker for silent partial failures** — paginated sources
   soft-fail mid-run (`_get_page` returns "no more pages" on error), so a
   "successful" fetch can be half-empty. `ingest._fetch_looks_complete`
   skips marking when the fetch returned 0 events or < 50% of the source's
   stored future rows (`MIN_FETCH_RATIO`).
3. **Flag + self-heal** — `db.mark_missing` stamps `events.missing_since`;
   the upsert clears it the moment any later run re-sees the event. A false
   stamp lasts at most until the next successful nightly run.
4. **Read-time grace** — tools only surface `possibly_cancelled: true` when
   the stamp is > 30h old (`_MISSING_GRACE_HOURS`), i.e. the event was
   missed by two consecutive nightly runs.

Participation is opt-in via `Source.window_days` (set by the six
full-window sources). **`mommy_poppins` must stay excluded** — its sitemap
lastmod discovery is incremental, so an unmodified event page legitimately
drops out of a run while the event is still on. Apply the same caution to
any future source that doesn't re-fetch its entire window every run.

`mark_missing` only stamps rows with `start_dt` inside the fetched window
minus one day of margin (sources truncate window ends to date boundaries),
and never re-stamps — grace is measured from the first miss. Staleness is
measured against the source's own successful runs (stamps only happen
during one), so a dead ingest cron flags nothing.

## HTTP security baseline (established Checkpoint C)

If you touch the server, do not regress these:

- All bearer comparisons via `secrets.compare_digest`. Never `==`.
- Redirect URI allowlist on `/authorize` GET and POST (defense in depth).
- Consent page sends `X-Frame-Options: DENY`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: no-referrer`, full CSP including
  `default-src 'none'` + `form-action 'self'` + `frame-ancestors 'none'`.
- Rate limiter on `/authorize` POST, `/token`, `/register`. Sliding-window
  per (client_ip, endpoint) buckets.
- `forwarded_allow_ips` defaults to `127.0.0.1`. Override only for Docker
  bridge networks via `FORWARDED_ALLOW_IPS` env.
- Browser-probe response is the minimum payload (`authorization_required`
  + `resource_metadata` only). Don't add identifying fields back.
- Master token never logged. No auth tokens in query strings.

Known accepted residuals (see `git log` for the security-audit commit):
- Auth code in URL on redirect — mitigated by single-use + 5-min TTL +
  `Referrer-Policy: no-referrer`.
- No persistent log scrubbing — `/authorize?...` query string lands in
  uvicorn access logs. Acceptable for personal scale; revisit if logs
  ship off-host.
- DCR is a no-op (accepts any payload) — intentional per OAuth spec for
  public clients; gating is at consent.

## Phase roadmap

- **Phase 1 (done):** `tvpp-9vvx` only. Permit data, no descriptions, all
  ingested rows `low_confidence: true`. ~700 events / 60-day window.
- **Phase 2 (done):** editorial scrapers — real descriptions, URLs,
  age ranges. The buildable backlog is cleared (every CONFIRMED venue is
  built, rejected, or deferred to Phase 3 — see below). Adding more sources
  still follows `.claude/agents/source-adder.md`.
  - **Live:** Mommy Poppins, BPL, Brooklyn Children's Museum, Green-Wood
    Cemetery, Prospect Park Alliance, New York Transit Museum, Brooklyn
    Army Terminal, Industry City, Governors Island, Domino Park.
  - **Rejected — no event feed:** Time Out NY Kids (`timeout_nykids.py`
    stub kept). JS-rendered editorial site; no structured data, no API,
    no sitemap with events. Needs headless browser — out of scope.
  - **Rejected — feed works, content isn't kid-relevant:** Coney Island USA.
    Squarespace JSON confirmed working, but the calendar is adult
    programming wholesale (burlesque/sideshow/drag; ~2% historical kid
    yield) and the Mermaid Parade is published outside the event feed.
    See the Coney Island USA entry in SOURCES-BACKLOG.md (Rejected section)
    for the evidence and revisit conditions.
- **Phase 2 backlog — venue sources (see `SOURCES-BACKLOG.md` for full
  probe instructions and data shapes):**
  - **BUILT (live):** Brooklyn Army Terminal — single-page HTML scrape via
    `curl_cffi`. Filters out "Live Music Concert" 21+ EDM shows. As built
    (2026-06-15): 24 cards → 12 dropped, 12 kept kid-relevant community
    events. See SOURCES-BACKLOG.md as-built block.
  - **BUILT (live):** Industry City — WordPress + The Events Calendar (Tribe),
    the same fast-path as Green-Wood / Prospect Park / NY Transit
    (`wp-json/tribe/events/v1/events`, `curl_cffi` impersonate=chrome). The
    earlier "custom headless CMS, no wp-json" verdict was a probe artifact.
    Categories aren't kid-curated, so filtering is title/description
    keyword-driven with `Nightlife` as a hard-exclude category and an adult
    blocklist (21+, burlesque, drag, late-night, "no children"). As built
    (2026-06-20): a live 60-day fetch returned 29 rows → 16 dropped, 13 kept
    (workshops, Puppetworks, Zine Club, outdoor World Cup watch parties).
    (Alcohol-tasting terms — cocktail/whiskey/sake/brewery/distillery/wine-or-
    beer tasting/happy hour — were later removed from the blocklist per the
    filter review, so gourmet-tour and sake-class rows are now kept too.)
    `cost`/`venue` always empty upstream →
    price UNKNOWN, venue/borough hardcoded Industry City / Brooklyn, no
    lat/lng/age. See SOURCES-BACKLOG.md as-built block.
  - **BUILT (live):** Governors Island — the prior "custom CMS, no API
    surface" verdict was a non-impersonating-probe artifact (same failure mode
    as Industry City). It has a clean custom Craft CMS / Solspace-Calendar JSON
    feed at `/things-to-do.json` (NOT WordPress/Tribe). Inclusive + blocklist
    filtering: GI skews family, so include by default and drop only clearly
    adult content (galas, 7AM NYCRUNS road races) and non-event amenities (bike
    rentals, the QC NY spa, the digital guide). As built (2026-06-20): a live
    fetch returned 100 rows → 15 dropped, 85 kept. Dates are "floating" local
    wall-time mislabeled `Z` (parsed as America/New_York). `cost` absent → price
    UNKNOWN; venue/borough hardcoded Governors Island / Manhattan; no
    lat/lng/age. **Opted OUT of missing-detection** (`window_days=None`): the
    feed hard-caps at 100 rows ordered id-asc with no pagination, so newer
    events can scroll past the cap rather than being cancelled. See
    SOURCES-BACKLOG.md as-built block.
  - **BUILT (live):** Domino Park — the "Sanity headless, no public feed"
    verdict was a non-impersonating-probe artifact. The `production` dataset on
    Sanity project `4shd8slw` allows anonymous reads, so we query the public
    GROQ API directly (`*[_type=="event"]{...}`, `curl_cffi`) — no scraping, no
    headless browser. Inclusive + light blocklist (it's a curated family-park
    feed, tags dominated by "Family & Education"). **Recurrence is keyed off
    the `variant` field, NOT `frequency`:** `reoccurring` docs are expanded via
    frequency/interval into per-occurrence rows (`external_id=f"{_id}:{date}"`);
    `single-day`/`multi-day` docs are one event each and their leftover
    frequency/endDate is vestigial template data (must be ignored or rows
    double-count). Free-text `startHour`/`endHour` parsed leniently; dates are
    local wall-time → America/New_York. Has lat/lng + descriptions + tags; no
    price → UNKNOWN; venue/borough hardcoded Domino Park / Brooklyn. Opted INTO
    missing-detection (`window_days=60`, full GROQ re-fetch, deterministic
    occurrence ids). As built (2026-06-20): 125 docs → 104 events over a 60-day
    window. See SOURCES-BACKLOG.md as-built block.
- **Phase 3 (in progress — see `PHASE-3-PLAN.md`):** location-awareness,
  weather on outdoor events, an indoor/outdoor heuristic flag, more venue
  sources, and deferred tech debt. AI/LLM enrichment is explicitly out of scope.
  - **DONE — A1 neighborhood coding + geocoding** (this pass): the `enrich.py`
    second pass codes `neighborhood` for every locatable row and backfills
    `lat`/`lng` as a side effect (see "Neighborhood coding" above). `near_me` /
    distance-from-home is the remaining A1 piece (the coords it needs now exist).
  - **TODO:** weather (A3, depends on coords + indoor/outdoor), indoor/outdoor
    flag (A2), tech-debt #4/#5 are closed; more venue sources (Workstream B).
  **Brooklyn Cyclones**
  is parked here too: the MLB Stats API (`teamId=453`, public JSON, no auth)
  gives the game schedule cheaply, but the themed nights (Star Wars Night
  etc.) that make it worth shipping live in Contentful CMS, JS-rendered only —
  a headless browser, drawn as the Phase 2 boundary. See SOURCES-BACKLOG.md
  § "The themed-night problem".

## Out-of-scope (deliberate)

- Multi-user. Single-user personal server; the OAuth shim trusts any client_id.
- Federated identity / SSO.
- Admin UI / browser config. The Claude client IS the UI.
- HTTP retries / queue workers. SQLite + sync httpx is fine at this scale.

## Local container dev

The base `docker-compose.yml` pulls `ghcr.io/yesfinethankyou/nyc-kids-mcp:latest`
— that's what the NAS uses. For local testing before pushing a tag:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

The `docker-compose.dev.yml` override:
- Adds `build: .` so the image is built from the current tree
- Tags it `nyc-kids-mcp:dev` so it doesn't shadow the GHCR `:latest` tag
- Runs the container as host UID 1000:1000 — the bind-mounted `./data` is
  owned by the host user, and the image's app user (uid 10001) can't write
  to it under that mount. The override is a dev-only workaround; the NAS
  flow uses 10001 and a host directory chowned to match.
- Profile-disables Watchtower (no point polling for updates locally)

## Docker conventions (Checkpoint D)

- Production image: `ghcr.io/yesfinethankyou/nyc-kids-mcp` (public). Built
  multi-arch (amd64 + arm64) by `.github/workflows/docker-publish.yml`.
- The runtime image hardcodes `DB_PATH=/data/events.db` and
  `OAUTH_DB_PATH=/data/oauth.db` via ENV. Compose mounts `./data` from the
  host onto `/data`. Don't move these paths without updating both files
  AND the README "Deploy" section.
- Image runs as non-root (uid/gid 10001). If you add a step that writes
  outside `/data`, it will fail — that is by design.
- Container exposes the server only. **Nightly ingest is a `docker exec`
  cron on the host (DSM Task Scheduler)**, not an in-container cron.
  Adding cron inside the container races with Watchtower restarts.
- Host port is bound to `127.0.0.1:8765:8765`. Tailscale Funnel on the host
  is the only path in. Do not change to `0.0.0.0` — that exposes the
  service on the LAN unnecessarily.
- Watchtower is scoped via `com.centurylinklabs.watchtower.enable=true`
  label so it does not auto-update other containers on the NAS. If you
  add a sidecar that should NOT auto-update, just omit the label.
- Healthcheck is a TCP-port probe (no curl in slim, no auth needed). If
  you want a richer probe, add an unauthenticated `/healthz` endpoint
  rather than baking the master token into the healthcheck.

## Files that must never be committed

- `data/*.db*` — events, oauth, WAL, SHM. Gitignored.
- `.env` — secrets live there in prod. Use `.env.example` as the template.
- `.venv/` — Python virtualenv.

If you ever see one of these proposed for `git add`, stop and ask.
