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
.venv/bin/python -m nyc_events.dashboard        # read-only tailnet dashboard (port 8766, no secrets needed)
.venv/bin/python -m nyc_events.ingest           # one-shot ingest (runs the enrich pass at the end)
.venv/bin/python -m nyc_events.enrich           # second pass alone: code new/uncoded rows (network)
.venv/bin/python -m nyc_events.enrich --recode-all  # re-resolve EVERY row (after static-table changes)
.venv/bin/python -m nyc_events.seed_fake        # populate fake events for connector smoke-testing
.venv/bin/python -m nyc_events.users add <name> # invite-code admin (add | revoke | list)
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

## Issue labeling (2026-07-07)

Every issue gets exactly one label from **Type** and one from **Priority**;
**Status** and **Area** are optional/supplementary. Apply all of these via
`issue_write`'s `labels` array (it silently creates a label if the name
doesn't already exist — see the tool-gap note below — so a typo'd label name
won't error, it'll just create a stray one; double-check spelling).

- **Type** (`type:`, pick one): `bug` (code does something other than
  intended), `data-quality` (code runs as designed but the data it produces
  is wrong/misleading — bad tags, wrong borough, dropped upstream detail;
  this repo's largest issue class), `security` (auth/OAuth/rate-limiting/
  injection/secrets — always gets extra review regardless of priority),
  `enhancement` (new capability, or a deliberate improvement to something
  that already works correctly), `chore` (refactor, dependency bump,
  doc-only, housekeeping — no user-facing behavior change).
- **Priority** (`priority:`, pick one): `P0` (incorrect output/crash/
  corruption in normal operation), `P1` (incorrect behavior in common edge
  cases), `P2` (incorrect behavior in rare edge cases), `P3` (minor/cosmetic).
  This is the label — **don't also embed `[P0]` etc. in the issue title**;
  the title-bracket convention predates the label existing for every tier
  and is now redundant (and driftable).
- **Status** (`status:`, open issues only — closed issues use GitHub's
  `state_reason`, not a label): `triage` (not yet verified/reproduced),
  `ready` (verified, scoped, safe to start without re-deriving context),
  `in-progress` (pair with a linked PR), `blocked` (external dependency —
  **always leave a comment explaining what unblocks it**, e.g. issue #41).
- **Area** (`area:`, multi-select, 1–2 typical): `auth` (`auth.py`/
  `oauth.py`/`users.py` — the do-not-regress surface), `sources` (any
  scraper module), `db` (`db.py`, schema, migrations, FTS), `ingest`
  (`ingest.py`/`enrich.py`, missing-detection, telemetry), `tools`
  (`tools.py`, the MCP surface/projections), `infra` (Dockerfile, compose,
  CI). Flags "review this more carefully" (`area:auth`) beyond what priority
  alone captures.

**Provenance stays in the issue body, not a label**: "Finding from X review
(date)" + the session URL is already a consistent convention across this
repo's issues — searchable text, no taxonomy needed for it.

**Tool-gap note**: the GitHub MCP server available in this environment has no
label create/update/delete endpoint — only `get_label` (read) and
`issue_write`'s `labels` field, which *does* auto-create an unrecognized
label name but only with a flat default color (`#ededed`) and empty
description. There's no way to set color/description via these tools; that
has to be done once, by hand, in the repo's Settings → Labels UI. Colors are
cosmetic only — the taxonomy is fully functional (filterable, searchable)
without them.



One home per fact class; prose that duplicates code drifts (a stale CLAUDE.md
claim is worse than none — agents trust it completely):
- **Per-source behavior** (quirks, filter strategy, id semantics) → the
  module docstring. CLAUDE.md keeps cross-cutting invariants only;
  SOURCES-BACKLOG.md keeps probe history. When you touch a source and find
  the same fact in two of these, prune the copy that isn't the docstring.
- **`session-handoff.md` is not append-forever**: keep the last ~3 sessions
  verbatim; when adding a new entry, feel free to compress entries older than
  that to a one-line summary + PR link. The hook only checks the file was
  touched.

## Layout

- `src/nyc_events/models.py` — `Event` + `Borough` / `Price` enums + `compute_id()`
- `src/nyc_events/db.py` — split SQLite stores (events + oauth) with idempotent migrations
- `src/nyc_events/server.py` — composition root only: `build_app()` (routes +
  middleware wiring) + `main()` (uvicorn). A diff here is always a wiring change.
- `src/nyc_events/tools.py` — the MCP surface: `FastMCP` instance, the seven
  tools, and the `_event_summary`/`_event_detail` projections. The high-churn
  side — new tools go here and must not touch `auth.py`.
- `src/nyc_events/auth.py` — the "do not regress" security surface: bearer
  middleware (+ OAuth token cache), rate limiter, redirect-URI allowlist,
  OAuth discovery/`/register`/`/authorize`/`/token` handlers, consent page.
- `src/nyc_events/dashboard.py` — the read-only tailnet dashboard
  (DASHBOARD-PLAN.md, shipped): its own Starlette app + `python -m
  nyc_events.dashboard` entry point on `config.DASHBOARD_PORT` (8766).
  GET routes only; DB access only via `db.connect_events_ro` (`mode=ro` —
  it never calls `init_events`); never opens `oauth.db`. **Import rule:
  `db` + `config` + the sources registry only** — importing `auth` or
  `tools` from it is the same red flag as a tool PR touching `auth.py`.
  Exposed via `tailscale serve` (tailnet-only), NEVER Funnel; tailnet
  membership is the auth, so no login code exists (or should).
- `src/nyc_events/config.py` — env-derived settings (`DB_PATH`,
  `OAUTH_DB_PATH`, `PORT`, `DASHBOARD_PORT`, `FORWARDED_ALLOW_IPS`,
  `OAUTH_TOKEN_TTL_DAYS`, redirect allowlist), read once at import. Consumers do attribute access
  (`config.DB_PATH`) so tests monkeypatch attributes here. Credentials
  (`MCP_AUTH_TOKEN`/`MCP_CONSENT_PASSWORD`) deliberately stay call-time
  env reads in auth.py/server.py.
- `src/nyc_events/oauth.py` — auth-code issue/consume + PKCE verification
- `src/nyc_events/users.py` — per-person invite codes (MULTI-USER-PLAN.md
  Phase A): PBKDF2 passcode hashing, `match_user()` for the consent flow,
  and the `add`/`revoke`/`list` admin CLI. Only hashes are stored; the
  plaintext code is printed once by `add`.
- `src/nyc_events/ingest.py` — CLI loop over `ENABLED_SOURCES`; runs `enrich` at the end
- `src/nyc_events/enrich.py` — second-pass location enrichment (neighborhood coding + lat/lng backfill)
- `src/nyc_events/geocode.py` — US Census geocoder client (forward + reverse; no API key)
- `src/nyc_events/data/` — committed open-data tables: `tract_to_nta.json`
  (census tract → NTA neighborhood), `park_neighborhoods.json` (park name → NTA),
  `library_neighborhoods.json` (borough+library-core → NTA). Built by
  `scripts/build_*.py`; loaded as package data.
- `src/nyc_events/sources/base.py` — `Source` ABC; each source is one file in the same dir
- `src/nyc_events/sources/_tribe.py` — shared machinery for the four
  WordPress / The Events Calendar sources (Green-Wood, Prospect Park,
  NY Transit Museum, Industry City): `TribeEventsSource` (fetch/pagination
  loop + curl_cffi page fetch), `parse_row`/`RowParts` (the common row
  skeleton), and the canonical `strip_html`/`parse_utc_dt`/`parse_cost`.
  A new Tribe venue subclasses this — never copy-adapt an existing Tribe
  source. Kid-relevance strategy, tag rules, and venue/borough/price
  mapping stay per-source; each module keeps a module-level `_parse_row`
  (assigned into the class via `staticmethod`) so parser tests exercise
  it directly with fixture dicts.
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

The server is split on churn vs consequence: `tools.py` changes often
(Phase 3 keeps adding tools), `auth.py` holds the security baseline and
should barely ever change. Never blend them back — a tool PR whose diff
touches `auth.py` is a red flag.

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
- `tests/test_dashboard.py` — the tailnet dashboard's HTTP surface via
  Starlette TestClient (rendering, filter threading, 400/404 paths, the
  XSS guard on scraped fields, the GET-only contract, missing-DB page).
  The db-layer numbers (`source_health`/`catalog_stats`/`connect_events_ro`)
  are covered in `test_db.py`.
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
   config. **Do NOT fix this with `FORWARDED_ALLOW_IPS=*`** (we used to;
   issue #33): Funnel forwards the client's own `X-Forwarded-For` header,
   and under a wildcard uvicorn takes the *leftmost* XFF entry as the
   client IP — an attacker mints a fresh per-IP rate-limit bucket on every
   request and the `/authorize`/`/token`/`/register` limits stop bounding
   online guessing. `docker-compose.yml` instead pins the bridge subnet
   (`172.28.0.0/24`) and trusts exactly the gateway
   (`FORWARDED_ALLOW_IPS=172.28.0.1`); uvicorn walks XFF right-to-left
   past that trusted hop to the Funnel-appended real client address, which
   can't be forged. If you change the subnet, change `FORWARDED_ALLOW_IPS`
   with it — and re-verify the discovery JSON still advertises `https://…`.

## OAuth model

- `MCP_AUTH_TOKEN` = master bearer AND fallback consent-page password. Stays
  operator-only — never hand it to an invited user.
- `MCP_CONSENT_PASSWORD` = optional separate consent-page password for `/authorize` POST.
  When set, the browser form accepts this instead of `MCP_AUTH_TOKEN`, so the master
  bearer is never typed into a browser. Falls back to `MCP_AUTH_TOKEN` when unset
  (original single-var behaviour). The two credentials can be rotated independently.
- **Per-person invite codes (multi-user, Phase A of MULTI-USER-PLAN.md):**
  the `users` table in `oauth.db` holds trusted friends/family. The consent
  page accepts EITHER the operator password OR a non-revoked user's invite
  code (`users.match_user`); the matched `user_id` rides the auth code and is
  stamped onto the issued token (`oauth_tokens.user_id`; NULL = operator).
  Codes are generated (`secrets.token_urlsafe(24)`), stored as salted PBKDF2
  hashes only, and managed via `python -m nyc_events.users add|revoke|list`.
  `revoke` tombstones the user AND deletes their tokens (live sessions die
  within the ~5-min token cache TTL — no cache-invalidation plumbing, by
  design).
- `oauth_tokens` table = OAuth-issued access tokens, stored **hashed at rest**
  (Phase B — a leaked oauth.db backup doesn't leak live sessions). Lives in
  `data/oauth.db`, intentionally separate from `data/events.db` so wiping
  events during dev does not log claude.ai out.
- **Rotating `MCP_AUTH_TOKEN` does NOT revoke already-issued access tokens.**
  This asymmetry is intentional. To revoke an invited user:
  `python -m nyc_events.users revoke <name>`. To revoke one of the operator's
  own connectors: `DELETE FROM oauth_tokens WHERE client_id = ?` on
  `data/oauth.db`.
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

- **Schema DDL + migrations live in `init_events()` / `init_oauth()`, run once
  at startup — NOT on the per-connection read path (issue #28).** `connect_events`
  / `connect_oauth` are now plain opens (WAL + `row_factory` + FK pragma, no
  DDL); they assume `init_*` already ran for that path. Call `init_*` at the top
  of each entry point: `server.build_app()`, `ingest.main`, `enrich.main`,
  `seed_fake.main` (and in tests that create a fresh DB — see the fixtures).
  Keeping DDL off `connect` means a `search_events` call never takes a write
  lock to re-run `CREATE TABLE`/`ALTER` and contend with the nightly ingest.
- Each `init_*` runs an idempotent `_migrate_*` after schema creation.
- Migrations are `PRAGMA table_info` to read existing columns + `ALTER TABLE
  ADD COLUMN` if missing. No timestamp-based migration framework — keep it
  this simple unless we genuinely outgrow it.
- Two SQLite files: `data/events.db` (data) and `data/oauth.db` (tokens).
  They have separate schemas. Do not cross-reference.
- Precedents: `events.raw_payload TEXT`, `oauth_tokens.expires_at TEXT`.
- The `geocode_cache` table lives in `events.db` (it's event-derived). It's a
  plain `CREATE TABLE IF NOT EXISTS` in `EVENTS_SCHEMA`, not a `_migrate_*`
  column-add — a whole new table is idempotent on its own.
- **Ingest telemetry (issue #65):** the `ingest_runs` table (also a plain
  `CREATE TABLE` in `EVENTS_SCHEMA`) records one row per source per nightly run
  (`fetched`/`inserted`/`updated`/`marked_missing`/`duration_s`/`outcome`,
  grouped by `run_id`). `ingest.main` writes it on every source exit path and
  compares each source's `fetched` against the median of its recent successful
  runs (`db.fetch_drift_baseline`, needs ≥3 prior `ok` runs); a fetch below
  `DRIFT_RATIO` (60%) of that median prints a warning and makes the run exit
  **4**. Self-bootstrapping: an empty table never false-alarms. This is the
  data model the planned dashboard's `source_health()` reads.
- **Never run `VACUUM` on `data/events.db` without immediately rebuilding the
  FTS index.** `events` has a TEXT primary key (no `INTEGER PRIMARY KEY` alias),
  so SQLite may renumber its implicit rowids on VACUUM. `events_fts` is an
  external-content FTS5 table keyed on those rowids; after a renumber the
  full-text index silently desynchronizes and returns wrong results. Fix with:
  `INSERT INTO events_fts(events_fts) VALUES('rebuild');`

## Source-data hygiene philosophy

The Phase-1 source (`tvpp-9vvx`) — **disabled 2026-07-12** (maintainer call:
its all-low-confidence rows went unused once `nycgovparks_events` covered the
Parks calendar; module + tests kept, re-enable steps in its docstring) — is a
permit registry, not a curated event listing. The philosophy below still
governs any future registry-style source. **Aggressive filtering is
correct**, not over-engineering:
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
age-band). The cross-source filter-consolidation pass is **done** (maintainer
review, 2026-06; the FILTER-REVIEW.md worksheet that drove it was deleted after
the review — the durable outcome lives in `sources/_filters.py` and the
per-source modules): alcohol-
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
- Default `limit=10` (`search_events` defaults to `limit=15`; all cap at 50).

Shared listing filters (threaded into `db.search`):
- `exclude_low_confidence` (all three listing tools) drops permit-style rows
  where `description IS NULL AND url IS NULL` — the "only curated, attendable
  events" path. Mirrors the `low_confidence` output flag.
- `source` + arbitrary date window (`start_date`/`end_date`, falling back to
  `days_ahead` width) are `search_events`-only. `start_date` defaults the window
  start to that local date instead of now; `end_date` omitted → `start +
  days_ahead`. End-before-start raises `ValueError`.

`list_facets()` returns the distinct in-catalog values for the search facets
(`boroughs`, `neighborhoods`, `tags`, `sources`) so a caller can discover valid
filter values instead of guessing. Reflects only currently-ingested rows; tags
are unpacked from the per-row JSON array in Python (no json1 dependency).

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
sources — but a failed pass exits the ingest with code **3**. `ENRICH=0`
skips it for offline dev.

**Ingest exit codes** (highest precedence first): **0** = clean, **2** =
one or more sources failed, **3** = sources fine but the enrich pass failed,
**4** = sources + enrich fine but a source's yield dropped below its recent
norm (issue #65 — see "Ingest telemetry" below). The nightly cron should
alert on any non-zero; 4 is a soft "a scraper may be silently degrading"
signal, not a hard failure.

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

**Persistence (issue #27):** the upsert **preserves** enriched
`neighborhood`/`lat`/`lng` across nightly re-ingests — a source-provided value
still wins, and the coding resets to NULL only when the row's venue or borough
changed this ingest, so stale coding re-resolves the same night (see the CASE
expressions in `db.upsert_events`). Nightly enrich therefore touches only
new/changed rows, and a wholesale enrich failure delays coverage for those
rows but can no longer blank the whole catalog's neighborhoods. The flip side:
corrections to the static tables don't reach already-coded rows on their own —
run `python -m nyc_events.enrich --recode-all` after editing
`_neighborhoods.py` or rebuilding the `data/*.json` tables. Recode only ever
adds/updates coverage: a row whose re-resolution fails keeps its old label.
The enrich `UPDATE` fills `lat`/`lng` via `COALESCE` (never clobbers a
source-provided coord) and fires the FTS `events_au` trigger, so neighborhood
is searchable immediately.

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
must be added or the pass codes nothing for new freeform venues (sources still
commit; existing rows keep their labels; the ingest exits 3 only if the pass
*raises* — a reachable-but-blocked geocoder may just return misses, which are
cached as negatives, so also check `geocode_cache` when debugging).

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

- **Single-worker only.** The rate limiter, OAuth token cache (`auth.py`),
  and pending auth codes (`oauth.py`) are in-process dicts. Running uvicorn
  with `workers > 1` breaks the OAuth flow non-deterministically (code
  issued on one worker, consumed on another) and the failure mimics a
  broken claude.ai client. `main()` runs single-process; keep it that way.
- All bearer comparisons via `secrets.compare_digest`. Never `==`.
- Redirect URI allowlist on `/authorize` GET and POST (defense in depth).
- Consent page sends `X-Frame-Options: DENY`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: no-referrer`, full CSP including
  `default-src 'none'` + `form-action 'self'` + `frame-ancestors 'none'`.
- Rate limiter on `/authorize` POST, `/token`, `/register`. Sliding-window
  per (client_ip, endpoint) buckets.
- Per-token rate limit on the authenticated MCP path (60 req/min,
  `_MCP_TOKEN_LIMIT`) — availability protection so one runaway client
  (including the master bearer) can't starve the NAS. Buckets are keyed by
  the token's sha256, never the raw bearer.
- OAuth access tokens are stored **hashed** (`db.hash_access_token`,
  `sha256:<hex>`); the wire still carries the plaintext bearer, and
  `_migrate_oauth` hashed legacy plaintext rows in place once. Plain SHA-256
  is deliberate — tokens are 384-bit random, not passwords; don't "upgrade"
  it to PBKDF2 (that's for the human-carried invite codes in users.py).
- Request-body cap (`_MAX_BODY_BYTES`, 8 KB) on `/authorize` POST, `/token`,
  `/register` — the unauthenticated endpoints that parse bodies. Enforced on
  the stream, not just Content-Length, so chunked bodies can't bypass it
  (issue #34).
- `forwarded_allow_ips` defaults to `127.0.0.1`. Override only for Docker
  bridge networks via `FORWARDED_ALLOW_IPS` env — name the bridge gateway
  exactly, never `"*"` (a wildcard lets spoofed `X-Forwarded-For` defeat the
  per-IP rate limiter; issue #33).
- Browser-probe response is the minimum payload (`authorization_required`
  + `resource_metadata` only). Don't add identifying fields back.
- Master token never logged. No auth tokens in query strings.

Known accepted residuals (see `git log` for the security-audit commit):
- Auth code in URL on redirect — mitigated by single-use + 5-min TTL +
  `Referrer-Policy: no-referrer`.
- ~~No persistent log scrubbing~~ — closed in multi-user Phase B:
  `auth.RedactAuthorizeQueryFilter` (wired onto `uvicorn.access` in
  `server.main`) rewrites `/authorize?...` to `/authorize?[redacted]` in
  access-log lines.
- DCR is a no-op (accepts any payload) — intentional per OAuth spec for
  public clients; gating is at consent.

## Phase roadmap

- **Phase 1 (done, source since RETIRED):** `tvpp-9vvx` only. Permit data,
  no descriptions, all ingested rows `low_confidence: true`. ~700 events /
  60-day window. **Disabled 2026-07-12** — unused by the maintainer once
  `nycgovparks_events` shipped (see "Source-data hygiene philosophy").
- **Phase 2 (done):** editorial scrapers — real descriptions, URLs,
  age ranges. The buildable backlog is cleared (every CONFIRMED venue is
  built, rejected, or deferred to Phase 3 — see below). Adding more sources
  still follows `.claude/agents/source-adder.md`.
  - **Live:** Mommy Poppins, BPL, Brooklyn Children's Museum, Green-Wood
    Cemetery, Prospect Park Alliance, New York Transit Museum, Brooklyn
    Army Terminal, Industry City, Governors Island, Domino Park, NYC Parks
    website (`nycgovparks_events` — the live nycgovparks.org "Best for Kids"
    calendar: microdata scrape + in-page map blob so lat/lng comes free,
    ~2,430 events / ~55-day window; complementary to `tvpp-9vvx`, verified
    zero overlap — see SOURCES-BACKLOG.md "Major reassessment"), New York
    Family (`new_york_family` — Schneps-network Tribe API deliberately capped
    at 16 rows/query with broken pagination, so it day-walks a 35-day window
    with adaptive time slices; NYC-filtered by coordinate boxes; the first
    source with structured age bands; ~500 events/run — read the module
    docstring before touching it, the API is under active lockdown upstream).
  - **Rejected — no event feed:** Time Out NY Kids (`timeout_nykids.py`
    stub kept). JS-rendered editorial site; no structured data, no API,
    no sitemap with events. Needs headless browser — out of scope.
  - **Rejected — feed works, content isn't kid-relevant:** Coney Island USA.
    Squarespace JSON confirmed working, but the calendar is adult
    programming wholesale (burlesque/sideshow/drag; ~2% historical kid
    yield) and the Mermaid Parade is published outside the event feed.
    See the Coney Island USA entry in SOURCES-BACKLOG.md (Rejected section)
    for the evidence and revisit conditions.
- **Phase 2 backlog — venue sources: all BUILT (live).** One line + the
  load-bearing gotcha each; the full as-built notes (probe history, filter
  decisions, row counts, upstream quirks) are in each source's
  SOURCES-BACKLOG.md as-built block — read that before touching a source.
  - Brooklyn Army Terminal — single-page HTML scrape (`curl_cffi`);
    drops 21+ EDM "Live Music Concert" shows.
  - Industry City — fourth Tribe/WordPress source; categories aren't
    kid-curated so filtering is keyword-driven with `Nightlife` hard-excluded;
    `cost`/`venue` always empty upstream (hardcoded Brooklyn, price UNKNOWN).
  - Governors Island — custom Craft/Solspace JSON at `/things-to-do.json`
    (NOT Tribe); dates are "floating" wall-time mislabeled `Z` (parse as
    America/New_York); **opted OUT of missing-detection** — the feed
    hard-caps at 100 rows id-asc, so events scroll past the cap.
  - Domino Park — public Sanity GROQ API (project `4shd8slw`, anonymous
    reads); **recurrence keys off `variant`, NOT `frequency`** —
    `reoccurring` docs expand per-occurrence, single/multi-day docs' leftover
    frequency is vestigial (ignore it or rows double-count).
  - Industry City, Governors Island, and Domino Park were each first
    "rejected" by a non-impersonating probe — always re-probe with
    `curl_cffi` impersonate=chrome before believing "no feed".
- **Phase 3 (in progress — see `PHASE-3-PLAN.md`):** location-awareness,
  weather on outdoor events, an indoor/outdoor heuristic flag, more venue
  sources, and deferred tech debt. AI/LLM enrichment is explicitly out of scope.
  - **DONE — A1 neighborhood coding + geocoding**, complete: the `enrich.py`
    second pass codes `neighborhood` for every locatable row and backfills
    `lat`/`lng` as a side effect (see "Neighborhood coding" above). `near_me` /
    distance-from-home was considered and **declined as out of scope** — not
    tracked as remaining A1 work.
  - **TODO:** indoor/outdoor flag (A2), weather (A3 — needs coords + A2),
    more venue sources (Workstream B). Tech-debt #4/#5/#6 closed.
  **Brooklyn Cyclones**
  is parked here too: the MLB Stats API (`teamId=453`, public JSON, no auth)
  gives the game schedule cheaply, but the themed nights (Star Wars Night
  etc.) that make it worth shipping live in Contentful CMS, JS-rendered only —
  a headless browser, drawn as the Phase 2 boundary. See SOURCES-BACKLOG.md
  § "The themed-night problem".

## Out-of-scope (deliberate)

- Multi-*tenancy*. Friends-and-family multi-user is supported at the auth
  layer (per-person invite codes — see "OAuth model" and MULTI-USER-PLAN.md),
  but everyone sees the same shared catalog: no per-user data, preferences,
  or isolation. The OAuth shim still trusts any client_id. **Multi-user is
  COMPLETE AND FROZEN as of 2026-07-07** (Phases A–C shipped; maintainer call:
  no further phases — see the freeze note atop MULTI-USER-PLAN.md).
- Federated identity / SSO.
- Admin UI / browser config. The Claude client IS the UI. **Shipped narrow
  exception:** the read-only, tailnet-only health/browse dashboard
  (`dashboard.py` — see Layout and `DASHBOARD-PLAN.md`). Anything beyond
  that (writes, auth forms, public exposure) stays out of scope.
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
- Healthcheck is a TCP-port probe (no curl in slim, no auth needed). For
  a richer probe, point it at the existing unauthenticated `/healthz`
  route (`server.py`) rather than baking the master token into the
  healthcheck.

## Files that must never be committed

- `data/*.db*` — events, oauth, WAL, SHM. Gitignored.
- `.env` — secrets live there in prod. Use `.env.example` as the template.
- `.venv/` — Python virtualenv.

If you ever see one of these proposed for `git add`, stop and ask.
