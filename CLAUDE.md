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
.venv/bin/python -m nyc_events.ingest           # one-shot ingest from enabled sources
.venv/bin/python -m nyc_events.seed_fake        # populate fake events for connector smoke-testing
.venv/bin/python -m pytest tests/ -q            # full test suite (should always be green)
.venv/bin/python -m pytest tests/test_security_fixes.py::test_rate_limiter_is_per_ip -q  # single test
.venv/bin/ruff check                            # lint
```

If a change breaks tests, fix the change — don't loosen the tests.

## Layout

- `src/nyc_events/models.py` — `Event` + `Borough` / `Price` enums + `compute_id()`
- `src/nyc_events/db.py` — split SQLite stores (events + oauth) with idempotent migrations
- `src/nyc_events/server.py` — FastMCP + OAuth shim + bearer middleware + MCP tools
- `src/nyc_events/oauth.py` — auth-code issue/consume + PKCE verification
- `src/nyc_events/ingest.py` — CLI loop over `ENABLED_SOURCES`
- `src/nyc_events/sources/base.py` — `Source` ABC; each source is one file in the same dir
- `src/nyc_events/sources/__init__.py` — `ENABLED_SOURCES` registry
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
gate, the actual keyword/category lists, and known inconsistencies (blocklist
drift, dead Green-Wood blocklist, bare-substring tag false positives). A
filter-consolidation pass is pending maintainer review (see `SOURCES-BACKLOG.md`
→ Tech debt). When adding a source, pick the matching strategy — don't add a
filter to a curated kids feed (`mommy_poppins`, `bk_childrens_museum` have none
by design).

## Tool output shape

Listing tools (`search_events`, `events_this_weekend`, `events_on_date`)
return the **summary** dict via `_event_summary(ev)`:
- Token-efficient — drops `external_id`, `end_local`, `lat`, `lng`,
  `neighborhood`, `age_min`, `age_max`, `source`.
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
    keyword-driven with `Nightlife` as a hard-exclude category and an
    adult/alcohol blocklist (21+, burlesque, drag, sake/whiskey/cocktail
    tastings, "no children"). As built (2026-06-20): a live 60-day fetch
    returned 29 rows → 16 dropped, 13 kept (workshops, Puppetworks, Zine Club,
    outdoor World Cup watch parties). `cost`/`venue` always empty upstream →
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
- **Phase 3 (planned — see `PHASE-3-PLAN.md`):** location-awareness
  (geocoding + neighborhood + distance-from-home filter), weather on outdoor
  events, an indoor/outdoor heuristic flag, more venue sources, and deferred
  tech debt (issues #4/#5/#6). Designed but not yet implemented — AI/LLM
  enrichment is explicitly out of scope for this phase. **Brooklyn Cyclones**
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
