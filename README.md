# NYC Kids MCP

A personal MCP server that aggregates NYC family-friendly events from curated
sources, stores them in SQLite, and exposes them to Claude via streamable-HTTP
tools — designed for use from the Claude mobile app while out with a kid.

**Status:**
- Checkpoint A ✅ scaffold + FastMCP + OAuth 2.1 shim + connected to claude.ai via Funnel.
- Checkpoint B ✅ NYC Permitted Events (tvpp-9vvx) ingest, ~700 kid-relevant events / 60 days.
- Checkpoint C ✅ security audit + bundle B fixes (rate limiter, redirect allowlist, consent CSP, OAuth expiry).
- Checkpoint D ✅ Dockerfile + docker-compose + GHCR + Watchtower + GH Actions multi-arch publish.

**Why "Permitted Events" and not "Parks":** the spec originally named the
NYC Parks Events Listing (`fudw-fgrp`) SODA dataset, but it's been frozen
since 2019-12. The live successor is `tvpp-9vvx` (NYC Permitted Event
Information) — a citywide permitting catalog, broader and noisier. The
ingest filters to `event_agency='Parks Department'`, a kid-friendly event
type allowlist, a title blocklist (drops Eid/load-in/RC-plane noise), and
finally a kid-keyword filter (must match at least one tag). Phase 2 scrapers
(Mommy Poppins, BPL, Time Out NY Kids, Brooklyn Children's Museum) will
provide the higher-curated signal alongside this baseline.

## Architecture

```
RSS / ICS / SODA / scrapers  →  ingest (nightly cron)  →  SQLite (FTS5)  →  FastMCP HTTP  →  Claude
```

- Python 3.11+ (developed on 3.14)
- `mcp` SDK with FastMCP, streamable-HTTP transport
- SQLite + FTS5 for text search
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
up the new rows immediately (SQLite + WAL).

Don't bake the cron into the container — Watchtower restarts the container
on every image update, which would race with a long-running ingest.

### 4. Auto-updates via Watchtower

The compose file's Watchtower service polls every 5 minutes for new
`ghcr.io/yesfinethankyou/nyc-kids-mcp:latest` images. It only touches
containers carrying the `com.centurylinklabs.watchtower.enable=true` label,
so other containers on your NAS are untouched.

When a new tag is pushed to GitHub (`v0.2.0`, etc.), the GH Actions workflow
builds + pushes amd64 and arm64 images. The NAS picks up the update on the
next poll.

### Image tags

The CI publishes the following on every `vX.Y.Z` tag push:

- `:latest` (only from `main` branch pushes)
- `:vX.Y.Z`, `:X.Y`, `:X` (semver-derived)

For pinning in production, prefer `:vX.Y.Z` over `:latest` and disable
Watchtower auto-update for that container by removing the enable label.

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

# MCP endpoint requires Bearer token
curl -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{}'                                                # → 401 "unauthorized"

# Full MCP handshake — initialize, capture session id, list tools
TOKEN=<your-token>
SID=$(curl -s -D - -o /dev/null -X POST http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  | grep -i '^mcp-session-id' | tr -d '\r' | cut -d' ' -f2)

curl -X POST http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

curl -X POST http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
# → should show: search_events, events_this_weekend, events_on_date, list_sources
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
5. **Paste your `MCP_AUTH_TOKEN`** on the consent page and click Approve
6. claude.ai stores an issued access token; you should now see the 4 tools

That's it — there's no "API key" field anywhere. The master token's role is
just the password on that one consent page. After approval, claude.ai sends
a different opaque token (stored in your `oauth_tokens` SQLite table) on
every request. Revoking access = `DELETE FROM oauth_tokens` for that row.

#### OAuth flow under the hood

| Endpoint                                       | Purpose                                                        |
|-----------------------------------------------|----------------------------------------------------------------|
| `WWW-Authenticate` on 401 from `/mcp`         | Tells the client where the discovery metadata is               |
| `/.well-known/oauth-protected-resource`       | RFC 9728 — points at us as the authorization server            |
| `/.well-known/oauth-authorization-server`     | RFC 8414 — lists `/authorize`, `/token`, `/register`           |
| `POST /register`                              | RFC 7591 DCR — accepts anything, returns a generated client_id |
| `GET  /authorize`                             | Consent page (HTML form: paste master token)                   |
| `POST /authorize`                             | Validates token, issues auth code, 302 to `redirect_uri`       |
| `POST /token`                                 | Code + PKCE verifier → opaque access token (stored in SQLite)  |

The master `MCP_AUTH_TOKEN` is still accepted directly as a bearer for curl
testing — useful for diagnostics without going through OAuth.

## Tools exposed

| Tool                  | Purpose                                                                                          |
|-----------------------|--------------------------------------------------------------------------------------------------|
| `search_events`       | Free-text + filters (borough/age/free/days_ahead). Returns the cheap summary projection.         |
| `events_this_weekend` | Now → upcoming Sunday 23:59 local. Cheap summary projection.                                     |
| `events_on_date`      | Single YYYY-MM-DD in `America/New_York`. Cheap summary projection.                               |
| `get_event_detail`    | Drill into one event by `event_id`. Untruncated description + all metadata (no raw payload).     |
| `get_event_raw`       | Original upstream JSON for one event by `event_id`. For debugging or recovering aged-out detail. |
| `list_sources`        | Per-source counts + freshness, for diagnosing stale ingest.                                      |

The three listing tools share borough/age/free_only/limit filters and return a
small per-event "summary" dict (default `limit=10`, description truncated to
200 chars, plus `event_id` for follow-up calls). Drill into a result with
`get_event_detail(event_id)` or `get_event_raw(event_id)`.

## Data sources and their limits

This project deliberately starts with a Phase-1 source whose data is broad,
noisy, and structurally thin. Phase-2 sources will add richer editorial signal.
Knowing the difference avoids "why is the data so weak" surprise.

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
- **No structured neighborhood, lat, lng.**
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
that's the adapter behaving correctly — the upstream is thin. Phase 2
sources will fix the descriptive gap.

### Phase 2 (not implemented yet): curated editorial sources

Planned adapters with real descriptions, URLs, age ranges:

- **Mommy Poppins NYC** — RSS / structured HTML scraping
- **Brooklyn Public Library** — calendar scraping (no public RSS feed)
- **Prospect Park Alliance** — scraping
- **Time Out NY Kids** — scraping

These will land alongside `nyc_permitted_events` rather than replace it;
permit data is a useful denominator even with its thinness.

## Project layout

```
nyc-events-mcp/
├── pyproject.toml
├── .env.example
├── src/nyc_events/
│   ├── models.py         # Event + Borough/Price enums + compute_id
│   ├── db.py             # SQLite schema, FTS5, upsert, prune, search
│   ├── server.py         # FastMCP app + bearer middleware + tools + /healthz
│   ├── ingest.py         # CLI: loops enabled sources -> upsert -> prune  (Checkpoint B)
│   ├── seed_fake.py      # Hardcoded events for Checkpoint A; delete after B
│   └── sources/
│       ├── base.py             # Source ABC
│       ├── nyc_parks.py        # NYC Open Data SODA   (Checkpoint B)
│       ├── mommy_poppins.py    # stub                  (Phase 2)
│       ├── bpl.py              # stub                  (Phase 2)
│       ├── timeout_nykids.py   # stub                  (Phase 2)
│       └── bk_childrens_museum.py  # stub              (Phase 2)
├── data/                 # SQLite lives here; gitignored
└── tests/
    ├── test_db.py                 # filled at Checkpoint B
    └── test_nyc_parks_parse.py    # filled at Checkpoint B
```

## Env vars

| Var                            | Default                                                                                | Purpose                                                                                          |
|--------------------------------|----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| `MCP_AUTH_TOKEN`               | (required)                                                                             | Bearer token + master password on the OAuth consent page                                         |
| `PORT`                         | `8765`                                                                                 | Internal HTTP port (Funnel maps 443 → this)                                                      |
| `DB_PATH`                      | `data/events.db`                                                                       | SQLite file for events (safe to wipe during ingest iteration)                                    |
| `OAUTH_DB_PATH`                | `data/oauth.db`                                                                        | Separate SQLite file for OAuth access tokens, so wiping events DB keeps connectors authenticated |
| `FORWARDED_ALLOW_IPS`          | `127.0.0.1`                                                                            | Source IPs whose `X-Forwarded-*` headers uvicorn trusts. On Synology Docker include the bridge.  |
| `OAUTH_REDIRECT_URI_ALLOWLIST` | `https://claude.ai/api/mcp/auth_callback,http://localhost,http://127.0.0.1`            | Comma-separated prefix allowlist for OAuth `redirect_uri`. Blocks open-redirect / phishing.       |
| `OAUTH_TOKEN_TTL_DAYS`         | `90`                                                                                   | Default lifetime of an OAuth-issued access token. Bounds an undetected leak.                     |

### Auth rotation model

The master `MCP_AUTH_TOKEN` and OAuth-issued access tokens are independent:

- **Rotating `MCP_AUTH_TOKEN`** invalidates the consent-page password (used
  once per connector pairing) and the direct bearer for curl testing. It does
  **NOT** invalidate access tokens that claude.ai already holds — those keep
  working until they hit `OAUTH_TOKEN_TTL_DAYS` or you explicitly delete them.
- **Revoking a single connector**: `DELETE FROM oauth_tokens WHERE client_id = ?`
  on `data/oauth.db`.
- **Revoking everything**: `DELETE FROM oauth_tokens`. claude.ai will re-prompt
  for the master token on the next request.

This asymmetry is intentional: rotating the master token is cheap and shouldn't
disconnect an already-paired client; revoking a connector is a deliberate act.

### Tool output fields

In addition to the obvious event fields (`title`, `when_local`, `venue`, `borough`, etc.) tools return:

- `url` — the event's own page if the source has one (null for permit-source rows).
- `venue_map_url` — a Google Maps lookup link for the venue, synthesized from `venue + borough`. Useful when `url` is null.
- `low_confidence` — `true` when the row has no description AND no real URL. Almost always the case for `nyc_permitted_events` rows (permits, not curated events); tell the user before they make plans.
