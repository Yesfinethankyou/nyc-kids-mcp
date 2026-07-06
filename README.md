# NYC Kids MCP

A personal MCP server that aggregates NYC family-friendly events from curated
sources, stores them in SQLite, and exposes them to Claude via streamable-HTTP
tools — designed for use from the Claude mobile app while out with a kid.

**Status:**
- Checkpoint A ✅ scaffold + FastMCP + OAuth 2.1 shim + connected to claude.ai via Funnel.
- Checkpoint B ✅ NYC Permitted Events (tvpp-9vvx) ingest, ~700 kid-relevant events / 60 days.
- Checkpoint C ✅ security audit + bundle B fixes (rate limiter, redirect allowlist, consent CSP, OAuth expiry).
- Checkpoint D ✅ Dockerfile + docker-compose + GHCR + Watchtower + GH Actions multi-arch publish.
- Phase 2 ✅ editorial scrapers — buildable backlog cleared. **Shipped:** Mommy
  Poppins NYC (~233 events/run), Brooklyn Public Library, Brooklyn Children's
  Museum, Green-Wood Cemetery (~104 events/60d), Prospect Park Alliance
  (~307 events/60d), New York Transit Museum (~10 events/60d), Brooklyn Army
  Terminal (~12 events/60d), Industry City (~8 events/60d), Governors Island
  (~85 events/run), Domino Park (~104 events/60d) — real
  descriptions, URLs, and (where
  upstream provides them) age ranges, coordinates, prices. Rejected: Time
  Out NY Kids (no event feed without a headless browser) and Coney Island
  USA (feed works, but the calendar is adult programming). More venues in
  `SOURCES-BACKLOG.md`.
- Phase 3 🚧 in progress — **shipped:** neighborhood coding + lat/lng
  geocoding as a nightly enrichment pass after ingest (US Census geocoder,
  results cached; surfaced as a `neighborhood` field + `search_events`
  filter). **Remaining:** distance-from-home (`near_me`), weather on outdoor
  events, an indoor/outdoor flag, and more venue sources. Design in
  `PHASE-3-PLAN.md`.

**Why "Permitted Events" and not "Parks":** the spec originally named the
NYC Parks Events Listing (`fudw-fgrp`) SODA dataset, but it's been frozen
since 2019-12. The live successor is `tvpp-9vvx` (NYC Permitted Event
Information) — a citywide permitting catalog, broader and noisier. The
ingest filters to `event_agency='Parks Department'`, a kid-friendly event
type allowlist, a title blocklist (drops Eid/load-in/RC-plane noise), and
finally a kid-keyword filter (must match at least one tag). The ten Phase 2
editorial sources (see Status above) add higher-curated signal alongside this
baseline.

## Architecture

```
RSS / ICS / SODA / scrapers  →  ingest (nightly cron)  →  SQLite (FTS5)  →  FastMCP HTTP  →  Claude
```

- Python 3.11+ (developed on 3.14)
- `mcp` SDK with FastMCP, streamable-HTTP transport
- SQLite + FTS5 for text search
- `httpx` for most fetching; `curl_cffi` (Chrome impersonation) for sources
  behind Cloudflare TLS-fingerprinting (Mommy Poppins, Green-Wood Cemetery,
  Prospect Park Alliance, New York Transit Museum, Industry City, Governors
  Island, Domino Park)
- **Auth:** minimal single-user OAuth 2.1 + PKCE shim (claude.ai web requires it; bare bearer
  isn't an option). Master token also still works directly for curl testing.
- Docker target: Synology NAS; public HTTPS via Tailscale Funnel

## Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
cp .env.example .env
# Generate a token and put it in .env:
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Deploy (Docker + Synology + Tailscale Funnel)

The production target is a Synology NAS. The compose file ships two services:
the MCP server itself and a Watchtower instance scoped to update only this
container.

### 1. Pull and run

On the NAS, in a directory of your choice:

```bash
git clone https://github.com/Yesfinethankyou/nyc-kids-mcp.git
cd nyc-kids-mcp
cp .env.example .env
# Edit .env, set MCP_AUTH_TOKEN to a long random string:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

mkdir -p data
docker compose pull
docker compose up -d
```

The compose file binds the server to `127.0.0.1:8765` on the host, NOT
`0.0.0.0` — public reach is intentionally only via Tailscale Funnel, never
via the LAN.

### 2. Expose via Tailscale Funnel

On the same host:

```bash
sudo tailscale funnel --bg 8765
# → reports the public hostname, e.g. https://nas-name.tailnet.ts.net
```

That hostname is what you paste into claude.ai → Settings → Connectors as
the bare connector URL (no `/mcp` suffix — claude.ai treats the URL as the
MCP endpoint itself).

### 3. Nightly ingest cron

The container runs the **server**, not the ingest loop. Nightly ingest is a
separate one-shot. On Synology DSM, schedule it via Task Scheduler:

- **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**
- **User:** `root` (needed for Docker socket access)
- **Schedule:** Daily, 03:30 (or whenever your upstream feels least loaded)
- **Run command:**

  ```bash
  docker exec nyc-events python -m nyc_events.ingest
  ```

The ingest writes to the mounted `./data` volume so the running server picks
up the new rows immediately (SQLite + WAL). It also runs a second
**enrichment** pass at the end that codes each event's neighborhood and
backfills coordinates (US Census geocoder; results cached so it's fast after
the first run). The container therefore needs outbound HTTPS at ingest time.
Set `ENRICH=0` in the command's environment to skip the pass.

Don't bake the cron into the container — Watchtower restarts the container
on every image update, which would race with a long-running ingest.

### 4. Auto-updates via Watchtower

The compose file's Watchtower service polls every 5 minutes for new
`ghcr.io/yesfinethankyou/nyc-kids-mcp:latest` images. It only touches
containers carrying the `com.centurylinklabs.watchtower.enable=true` label,
so other containers on your NAS are untouched.

Watchtower itself is version-pinned in the compose file (it mounts the
Docker socket, which is root-equivalent on the host — it must not float on
`:latest`). Bump its tag deliberately. The app image's Python dependencies
are pinned via `requirements.lock` at build time, so a rebuild of the same
git commit produces the same dependency set; the residual trust is the
`:latest` app tag itself, which Watchtower auto-deploys by design — pin the
app image and drop Watchtower if you want a human in that loop (see below).

When a new tag is pushed to GitHub (`v0.2.0`, etc.), the GH Actions workflow
builds + pushes amd64 and arm64 images. The NAS picks up the update on the
next poll.

### Image tags

The CI publishes the following on every `vX.Y.Z` tag push:

- `:latest` (only from `main` branch pushes)
- `:vX.Y.Z`, `:X.Y`, `:X` (semver-derived)

For pinning in production, prefer `:vX.Y.Z` over `:latest` and disable
Watchtower auto-update for that container by removing the enable label.

### 5. Backups + uptime monitoring (multi-user Phase C)

Once other people depend on the connector, two bits of NAS ops matter
(see `MULTI-USER-PLAN.md` Phase C):

**Back up `oauth.db` nightly.** Losing it logs every user out at once (they'd
each need a fresh consent-page approval; invited users' codes still work, but
it's a multi-person annoyance). `events.db` needs no backup — the nightly
ingest rebuilds it. Don't just file-copy a live SQLite DB in WAL mode (torn
copy risk); snapshot it through SQLite's online backup API via the
container's Python. DSM Task Scheduler, daily (e.g. 04:30, after ingest),
user `root`:

```bash
docker exec nyc-events python -c "import sqlite3; s = sqlite3.connect('/data/oauth.db'); d = sqlite3.connect('/data/oauth.db.bak'); s.backup(d); d.close(); s.close()"
```

The snapshot lands in the bind-mounted `./data` on the host, so whatever
already backs up your NAS volumes (Hyper Backup etc.) carries it off-box —
make sure that directory is in a backup task. Restore = stop the container,
`cp data/oauth.db.bak data/oauth.db`, start it. The `.bak` contains hashed
tokens and hashed invite codes only, but treat it as sensitive anyway.

**External uptime check on `/healthz`.** Point any monitor at the **public
Funnel URL** — `https://<your-host>.ts.net/healthz`, expect HTTP 200 body
`ok` — so it exercises the whole path (Funnel + container), not just the
LAN port. `/healthz` is unauthenticated by design; never put the master
token in a monitor config. Self-hosted option on the same NAS
([Uptime Kuma](https://github.com/louislam/uptime-kuma)) is fine for alerting
on container death, but it shares the NAS as a failure domain — a free
external pinger (e.g. UptimeRobot) is the more honest check. The NAS +
Funnel remain a single point of failure by design; set that expectation
with users instead of engineering around it.

## Checkpoint A — verify the HTTP + auth + tools path

Seeds 6 hardcoded events across all 5 boroughs, starts the server, and proves
the path that Claude will use.

```bash
# 1. Seed fake events (idempotent — safe to re-run)
.venv/bin/python -m nyc_events.seed_fake
# → "Seeded 6 fake events to data/events.db: 6 inserted, 0 updated"

# 2. Run the server
MCP_AUTH_TOKEN=<your-token> .venv/bin/python -m nyc_events.server
# → "Uvicorn running on http://0.0.0.0:8765"
```

In another shell:

```bash
# Unauthenticated healthz works
curl http://127.0.0.1:8765/healthz                       # → "ok"

# MCP is served at the ROOT path (/), not /mcp — claude.ai treats the pasted
# connector URL as the endpoint itself (see streamable_http_path="/" in
# server.py). The endpoint requires a Bearer token:
curl -X POST http://127.0.0.1:8765/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{}'                                                # → 401 "unauthorized"

# Full MCP handshake — initialize, capture session id, list tools
TOKEN=<your-token>
SID=$(curl -s -D - -o /dev/null -X POST http://127.0.0.1:8765/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  | grep -i '^mcp-session-id' | tr -d '\r' | cut -d' ' -f2)

curl -X POST http://127.0.0.1:8765/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

curl -X POST http://127.0.0.1:8765/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
# → should show 7 tools: search_events, events_this_weekend, events_on_date,
#   get_event_detail, get_event_raw, list_sources, list_facets
```

### Adding as a custom connector in claude.ai web

claude.ai's web UI does OAuth discovery on custom connector URLs and won't
accept a pasted bearer token. The server exposes a minimal OAuth 2.1 shim
covered below. Quick recipe:

1. Expose the server publicly: `tailscale funnel --bg 8765`
2. In claude.ai → Settings → Connectors → Add custom connector
3. Paste the bare Funnel URL with NO path suffix: `https://nas.example.ts.net`
   (claude.ai treats the URL as the MCP endpoint itself — don't append `/mcp`)
4. claude.ai redirects you to a one-field consent page on your server
5. **Paste your access code** on the consent page and click Approve — as the
   operator that's `MCP_AUTH_TOKEN` (or `MCP_CONSENT_PASSWORD` if set); an
   invited user pastes their personal invite code (see below)
6. claude.ai stores an issued access token; you should now see the 7 tools

That's it — there's no "API key" field anywhere. The credential's only role is
the password on that one consent page. After approval, claude.ai sends
a different opaque token on every request (stored hashed in your
`oauth_tokens` SQLite table, so a leaked DB backup doesn't leak live
sessions).

#### Inviting friends & family

Each trusted user gets their own generated invite code instead of the shared
password, so one person can be revoked without rotating anything for everyone
else (see `MULTI-USER-PLAN.md`):

```bash
python -m nyc_events.users add "Aunt Kim"     # prints her invite code ONCE
python -m nyc_events.users list               # users + token counts
python -m nyc_events.users revoke "Aunt Kim"  # disables the code + deletes her tokens
```

Send the code over a reasonable channel; they paste it on the consent page in
step 5. Codes are stored as salted hashes only. Access tokens issued via an
invite code carry that `user_id`, so revocation is per-person; revoked
sessions stop working within ~5 minutes (server-side token cache).

#### Onboarding an invited user

Hand the new user two things — the **connector URL** (your public Funnel
hostname, e.g. `https://nas.example.ts.net`) and their **invite code** — then
give them the steps below. Everything from "Send this to the person you're
inviting" down is written to be copy-pasted to them verbatim; fill in the URL
first.

> **Setting up your NYC Kids events connector in Claude**
>
> You'll need: a Claude account (the [web app](https://claude.ai) or the
> mobile app both work), the connector URL and the invite code I sent you.
> This connects Claude to a shared calendar of NYC family-friendly events —
> once it's set up you can just ask Claude things like *"what's happening for
> kids in Brooklyn this weekend?"*
>
> 1. Open Claude and go to **Settings → Connectors** (on mobile: **Profile →
>    Settings → Connectors**).
> 2. Tap **Add custom connector**.
> 3. For the URL, paste the connector URL exactly as I sent it — nothing
>    added on the end. Give it any name you like (e.g. "NYC Kids Events").
> 4. Claude sends you to a small approval page that asks for an **access
>    code**. Paste the **invite code** I sent you and tap **Approve**.
> 5. Done — Claude will show a set of event-search tools. Start a new chat and
>    ask about kids' events in NYC to try it.
>
> A few notes:
> - **Keep your invite code.** You'll need it again if you add Claude on
>    another device, or if you're ever asked to reconnect. It's yours alone —
>    please don't share it.
> - If the approval page says the code is invalid, double-check you copied the
>    whole thing (no stray spaces), and that you used the *invite code* rather
>    than the URL. If it still fails, let me know — I can reissue it.
> - If Claude says the URL looks wrong, make sure you pasted it with nothing
>    appended after the hostname.

If a user needs a fresh code (lost it, or you revoked and want to re-add
them), `revoke` then `add` again — codes can't be recovered, only reissued,
since only the hash is stored.

#### OAuth flow under the hood

| Endpoint                                       | Purpose                                                        |
|-----------------------------------------------|----------------------------------------------------------------|
| `WWW-Authenticate` on 401 from `/` (the MCP endpoint) | Tells the client where the discovery metadata is        |
| `/.well-known/oauth-protected-resource`       | RFC 9728 — points at us as the authorization server            |
| `/.well-known/oauth-authorization-server`     | RFC 8414 — lists `/authorize`, `/token`, `/register`           |
| `POST /register`                              | RFC 7591 DCR — accepts anything, returns a generated client_id |
| `GET  /authorize`                             | Consent page (HTML form: paste access code)                    |
| `POST /authorize`                             | Validates operator password or invite code, issues auth code, 302 to `redirect_uri` |
| `POST /token`                                 | Code + PKCE verifier → opaque access token (stored in SQLite)  |

The master `MCP_AUTH_TOKEN` is still accepted directly as a bearer for curl
testing — useful for diagnostics without going through OAuth.

## Tools exposed

| Tool                  | Purpose                                                                                          |
|-----------------------|--------------------------------------------------------------------------------------------------|
| `search_events`       | Free-text + filters (borough/neighborhood/age/free/source/exclude_low_confidence) over an arbitrary date range (start_date/end_date or days_ahead). Returns the cheap summary projection. |
| `events_this_weekend` | Saturday 00:00 → Sunday 23:59 local of the current/upcoming weekend (starts now if mid-weekend). |
| `events_on_date`      | Single YYYY-MM-DD in `America/New_York`. Cheap summary projection.                               |
| `get_event_detail`    | Drill into one event by `event_id`. Untruncated description + all metadata (no raw payload).     |
| `get_event_raw`       | Original upstream JSON for one event by `event_id`. For debugging or recovering aged-out detail. |
| `list_sources`        | Per-source counts + freshness, for diagnosing stale ingest.                                      |
| `list_facets`         | Distinct filter values in the live catalog (boroughs, neighborhoods, tags, sources) for forming valid `search_events` filters. |

The three listing tools share borough/age/free_only/`exclude_low_confidence`/limit
filters and return a small per-event "summary" dict (`search_events` defaults to
`limit=15`, the others to `limit=10`; description truncated to 200 chars, plus
`event_id` for follow-up calls). `exclude_low_confidence=true` drops permit-style
rows that have no description and no URL — use it for "only curated, attendable
events." Drill into a result with `get_event_detail(event_id)` or
`get_event_raw(event_id)`.

## Data sources and their limits

This project deliberately starts with a Phase-1 source whose data is broad,
noisy, and structurally thin. The Phase-2 editorial sources add richer
editorial signal alongside it. Knowing the difference avoids "why is the data
so weak" surprise.

### Phase 1: `nyc_permitted_events` (NYC Open Data `tvpp-9vvx`)

What this is: a citywide **permit registry**, not a curated event listing.
Each row is "permission to use a public space on this date" — many rows
are private gatherings, school field days, league field reservations, and
religious observances rather than parent-attendable events.

Hard limits of this source:

- **Rolling 30-day window upstream.** Past events roll off; we can't refetch
  detail once they're gone. That's why every ingested row stores a
  `raw_payload` snapshot — `get_event_raw(event_id)` survives upstream drop.
- **No description.** The dataset has no descriptive text at all. Every
  ingested row therefore has `low_confidence: true` in tool output.
- **No URL.** No event landing page exists upstream. The server synthesizes
  a `venue_map_url` (Google Maps lookup of the venue) so the user has
  *something* clickable.
- **No organizer, no cost, no audience, no age fields.** Per-event price is
  always `unknown`; age range is always null.
- **No structured neighborhood, lat, lng** *in the feed* — but the nightly
  enrichment pass codes a neighborhood for most permit rows by matching the
  park name against an NYC open-data park→neighborhood table (~91% of rows),
  and geocodes the rest.
- **Heavy filtering required.** The parser applies an agency allowlist
  (`Parks Department` only), an `event_type` allowlist, a regex title
  blocklist (school identifiers like `PS \d+ / I.S. \d+`, religious phrases,
  load-in/load-out, RC-plane hobbies, etc.), and a kid-keyword tag filter
  (events without at least one matched tag are dropped as noise).
- **Recurring permits.** One `event_id` = one permit, often covering 30+
  recurring occurrences. The parser binds `external_id = "{permit_id}:{start_dt}"`
  so each occurrence is its own row instead of collapsing.
- **Rain-date hedges.** Some permits book both a primary date and a rain-day
  backup; the parser drops the row whose `start_dt` matches the explicit
  rain-date string in the title.

If you ask "why is the data so thin / why is everything `low_confidence`",
that's the adapter behaving correctly — the upstream is thin. The Phase 2
editorial sources fill the descriptive gap for venues they cover.

### Phase 2 (shipped): curated editorial sources

Adapters with real descriptions, URLs, age ranges:

- ✅ **Mommy Poppins NYC** — *shipped.* Sitemap URL discovery + JSON-LD detail
  scraping (uses `curl_cffi` to clear Cloudflare). ~233 events/run with
  descriptions, URLs, age ranges, coordinates, prices.
- ✅ **Brooklyn Public Library** — *shipped.* Calendar ingestion, kid programs
  across branches.
- ✅ **Brooklyn Children's Museum** — *shipped.*
- ✅ **Green-Wood Cemetery** — *shipped.* WordPress/Tribe Events REST API,
  keyword-filtered to kid-relevant programming (~104 events/60 days).
- ✅ **Prospect Park Alliance** — *shipped.* Same Tribe Events REST API,
  category-filtered (Kids, Audubon, Carousel, Lefferts, Nature Programs,
  Film, Performing Arts, Education); ~307 events/60 days.
- ✅ **New York Transit Museum** — *shipped.* Third Tribe Events REST API
  instance, category-filtered (Family Programs, Nostalgia Rides; members-only
  and virtual programs excluded); ~10 events/60 days — Transit Tots, family
  workshops, vintage-train rides.
- ✅ **Brooklyn Army Terminal** — *shipped.* Single-page HTML scrape
  (`curl_cffi` + selectolax); drops "Live Music Concert" 21+ EDM shows,
  keeps free community/family programming (Summer at the Terminal markets
  and food fests, cultural festivals, Rooftop Films, Día de Los Muertos);
  ~12 events/60 days.
- ✅ **Industry City** — *shipped.* Fourth Tribe Events REST API instance
  (`curl_cffi`); categories aren't kid-curated, so filtering is
  title/description keyword-driven with `Nightlife` hard-excluded and an
  adult blocklist (21+, burlesque, drag, late-night). Keeps maker/craft
  workshops, Puppetworks, Zine Club; `cost` and
  `venue` always empty upstream → price unknown, venue/borough hardcoded.
  ~8 events/60 days.
- ✅ **Governors Island** — *shipped.* Custom Craft CMS / Solspace-Calendar
  JSON feed (`/things-to-do.json`, `curl_cffi`) — NOT WordPress/Tribe; the
  earlier "no API surface" verdict was a probe artifact. Inclusive + blocklist
  filtering (GI skews family): drops galas, NYCRUNS road races, and non-event
  amenities (bike rentals, spa, digital guide). Dates are floating local
  wall-time; `cost` absent → price unknown, venue/borough hardcoded Governors
  Island / Manhattan. Opted out of missing-detection (feed caps at 100 rows,
  id-asc). ~85 events/run.
- ✅ **Domino Park** — *shipped.* Public Sanity GROQ API (project `4shd8slw`,
  anonymous reads; `curl_cffi`) — the "Sanity headless, no feed" verdict was a
  probe artifact. Recurrence keyed off the `variant` field (`reoccurring` docs
  expanded per-occurrence; `single-day`/`multi-day` kept as one event, their
  leftover frequency ignored). Inclusive + light blocklist (curated
  family-park feed). Has lat/lng + descriptions; no price → unknown,
  venue/borough hardcoded Domino Park / Brooklyn. ~104 events/60 days.
- ❌ **Time Out NY Kids** — *rejected.* JS-rendered editorial site, no
  structured feed; would need a headless browser (out of scope).
- ❌ **Coney Island USA** — *rejected.* Working Squarespace feed, but the
  calendar is adult programming (burlesque/sideshow) and the Mermaid
  Parade isn't published through it.

See `SOURCES-BACKLOG.md` for additional candidate venues. Industry City,
Governors Island, and Domino Park were all originally rejected by a probe that
didn't impersonate a browser (ate a 403) and have since been re-probed and
built — the lesson, now recorded in the backlog, is to always probe with
`curl_cffi` impersonation before concluding "no feed." Brooklyn Cyclones is
deferred to Phase 3 (its themed-night data needs a headless browser).

These land alongside `nyc_permitted_events` rather than replace it; permit
data is a useful denominator even with its thinness.

## Project layout

```
nyc-kids-mcp/
├── pyproject.toml
├── .env.example
├── src/nyc_events/
│   ├── models.py         # Event + Borough/Price enums + compute_id
│   ├── db.py             # SQLite schema, FTS5, upsert, prune, search
│   ├── server.py         # composition root: build_app() wiring + uvicorn main()
│   ├── tools.py          # FastMCP instance + the 7 MCP tools + projections
│   ├── auth.py           # bearer middleware, OAuth 2.1 shim, rate limiter
│   ├── oauth.py          # auth-code issue/consume + PKCE verification
│   ├── users.py          # per-person invite codes + add/revoke/list admin CLI
│   ├── config.py         # env-derived settings (DB paths, port, allowlists)
│   ├── ingest.py         # CLI: loops ENABLED_SOURCES -> upsert -> prune -> enrich
│   ├── enrich.py         # second-pass neighborhood coding + lat/lng backfill
│   ├── geocode.py        # US Census geocoder client (no API key)
│   ├── seed_fake.py      # hardcoded events for connector smoke-testing
│   ├── data/             # committed open-data lookup tables (tract/park/library → NTA)
│   └── sources/          # one module per source (+ shared _tribe/_filters/
│                         #   _neighborhoods helpers, ENABLED_SOURCES registry)
├── scripts/              # one-shot builders for the src/nyc_events/data tables
├── data/                 # SQLite lives here; gitignored
├── SOURCES-BACKLOG.md    # researched candidate sources + as-built notes
└── tests/                # per-surface tests + one test_<source>_parse.py per
                          #   source, against real captured fixtures/
```

See `CLAUDE.md` `## Layout` for the authoritative per-module map.

## Env vars

| Var                            | Default                                                                                | Purpose                                                                                          |
|--------------------------------|----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| `MCP_AUTH_TOKEN`               | (required)                                                                             | Bearer token + master password on the OAuth consent page                                         |
| `PORT`                         | `8765`                                                                                 | Internal HTTP port (Funnel maps 443 → this)                                                      |
| `DB_PATH`                      | `data/events.db`                                                                       | SQLite file for events (safe to wipe during ingest iteration)                                    |
| `OAUTH_DB_PATH`                | `data/oauth.db`                                                                        | Separate SQLite file for OAuth access tokens, so wiping events DB keeps connectors authenticated |
| `FORWARDED_ALLOW_IPS`          | `127.0.0.1`                                                                            | Source IPs whose `X-Forwarded-*` headers uvicorn trusts. In Docker, name the bridge gateway exactly (compose pins it to `172.28.0.1`). Never `"*"` — that lets a client spoof `X-Forwarded-For` and mint fresh per-IP rate-limit buckets on the OAuth endpoints. |
| `OAUTH_REDIRECT_URI_ALLOWLIST` | `https://claude.ai/api/mcp/auth_callback,http://localhost,http://127.0.0.1`            | Comma-separated allowlist for OAuth `redirect_uri`, matched by URL components (exact scheme+host, port if pinned, path prefix). Blocks open-redirect / phishing. |
| `OAUTH_TOKEN_TTL_DAYS`         | `90`                                                                                   | Default lifetime of an OAuth-issued access token. Bounds an undetected leak.                     |

### Auth rotation model

The master `MCP_AUTH_TOKEN` and OAuth-issued access tokens are independent:

- **Rotating `MCP_AUTH_TOKEN`** invalidates the consent-page password (used
  once per connector pairing) and the direct bearer for curl testing. It does
  **NOT** invalidate access tokens that claude.ai already holds — those keep
  working until they hit `OAUTH_TOKEN_TTL_DAYS` or you explicitly delete them.
- **Revoking one invited user**: `python -m nyc_events.users revoke <name>` —
  disables their invite code and deletes their attributed tokens.
- **Revoking a single connector** (e.g. one of your own sessions):
  `DELETE FROM oauth_tokens WHERE client_id = ?` on `data/oauth.db`.
- **Revoking everything**: `DELETE FROM oauth_tokens`. claude.ai will re-prompt
  for an access code on the next request.

This asymmetry is intentional: rotating the master token is cheap and shouldn't
disconnect an already-paired client; revoking a connector is a deliberate act.

### Tool output fields

In addition to the obvious event fields (`title`, `when_local`, `venue`, `borough`, etc.) tools return:

- `url` — the event's own page if the source has one (null for permit-source rows).
- `venue_map_url` — a Google Maps lookup link for the venue, synthesized from `venue + borough`. Useful when `url` is null.
- `low_confidence` — `true` when the row has no description AND no real URL. Almost always the case for `nyc_permitted_events` rows (permits, not curated events); tell the user before they make plans.
- `possibly_cancelled` — `true` when a future event has been missing from its source's feed for two consecutive nightly ingests. The event may have been cancelled upstream; confirm with the venue before making plans. (Flagged, never deleted — the flag clears itself if the event reappears.)
