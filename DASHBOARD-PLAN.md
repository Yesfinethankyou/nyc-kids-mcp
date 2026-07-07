# Dashboard plan — read-only tailnet web UI

Design for future work; nothing here is implemented yet. A small read-only
browser dashboard showing connector/ingest health plus an event browse/filter
page, reachable **only from inside the tailnet** — never via Tailscale Funnel.

## Why this exists / scope decision

CLAUDE.md lists "Admin UI / browser config — the Claude client IS the UI" as
deliberately out of scope. This plan is a **narrow, conscious exception**:
a *read-only status and browse* page, not an admin UI. Nothing here
configures, edits, or administers anything. The motivating gap: the only ways
to answer "did last night's ingest work?" today are an MCP call through
Claude (`list_sources`) or a dev-session skill (`/ingest-health`) — there is
no glanceable view from a phone.

Two hard constraints shape the whole design:

1. **Zero new public attack surface.** The MCP server sits on a public
   Funnel hostname behind OAuth designed for MCP clients, not browsers. A
   browser UI on that server would need a cookie/session login — new code in
   `auth.py`, the "do not regress" surface. So the dashboard is a **separate
   process on a separate port, exposed tailnet-only**; tailnet membership IS
   the auth. `auth.py`, `server.py`, and `tools.py` are untouched.
2. **Read-only by construction.** The dashboard opens `events.db` with
   `mode=ro`, serves GET routes only, and never touches `oauth.db` at all.

## Architecture

```
                    ┌──────────────────────────── NAS ─────────────────────────────┐
 claude.ai ──HTTPS──► Tailscale Funnel ──► 127.0.0.1:8765  nyc-events (MCP+OAuth)  │
                    │                                        │ rw                  │
                    │                                     ./data/events.db         │
                    │                                        │ ro (mode=ro URI)    │
 your phone ─tailnet► tailscale serve ───► 127.0.0.1:8766  nyc-events-dashboard    │
 (member device)    └──────────────────────────────────────────────────────────────┘
```

- **New module `src/nyc_events/dashboard.py`** — its own Starlette app +
  `python -m nyc_events.dashboard` entry point (uvicorn, own port, default
  `8766` via new `config.DASHBOARD_PORT`). It imports `db` and `config`
  only — importing `auth` or `tools` from it is the same red flag as a tool
  PR touching `auth.py`. If rendering helpers grow, split into a
  `dashboard/` package, but keep the import rule.
- **Same container image, second compose service** (see Deployment). A
  second process rather than a second port on the existing server keeps the
  single-worker MCP/OAuth process byte-identical and means a dashboard crash
  can't take the connector down (and vice versa).
- **No new dependencies.** Starlette + uvicorn are already installed. HTML
  is rendered with stdlib string templates + `html.escape` on every
  interpolated value — same approach as the consent page in `auth.py`. No
  Jinja, no JS framework; plain HTML forms + tables (a `<style>` block is
  fine; keep it self-contained, no CDN assets — tailnet pages shouldn't
  leak to third-party hosts).

## Routes (all GET; no POST anywhere)

| Route | Purpose |
| --- | --- |
| `/` | Health dashboard (the main deliverable) |
| `/events` | Browse/filter form + results table |
| `/event/{event_id}` | Single-event detail (untruncated description, raw-payload toggle can come later) |
| `/healthz` | Plain 200 for the container healthcheck |

### `/` — health dashboard

Per-source table, one row per source in `ENABLED_SOURCES` (LEFT-JOIN style:
a registered source with zero DB rows must still show, red — that's the
"scraper broke" signal `db.list_sources` alone can't give, since it only
GROUPs over rows that exist):

- `event_count`, future-event count, `earliest_event` / `latest_event`
- `MAX(last_seen)` with staleness highlighting: warn > 30 h (one missed
  nightly run — reuse the 30 h grace constant, don't invent a new number),
  red > 54 h (two missed runs)
- count of currently-flagged rows (`missing_since` older than the grace
  window) — the `possibly_cancelled` population
- `low_confidence` count (`description IS NULL AND url IS NULL`)

Catalog-level strip above the table: total events, total future events,
neighborhood coverage % (rows with `neighborhood IS NOT NULL`),
`geocode_cache` row count, DB file size, page-rendered-at timestamp.

The SQL for this lives in **`db.py`** as a new read-only
`source_health(conn, now)` function (precedent: `list_sources`,
`list_facets` — shared queries belong there, and it makes the numbers unit-
testable in `test_db.py` without HTTP). `list_sources` itself stays as-is;
it's the MCP tool's shape.

### `/events` — browse & filter

A form that maps 1:1 onto the existing `db.search` kwargs — the dashboard
adds **no new query semantics**:

- text query (FTS via the existing `_fts_query` escaping), borough
  (dropdown from `db.list_facets`), neighborhood substring, source
  (dropdown), age, `free_only`, `exclude_low_confidence`, date window
  (start/end date inputs → `start_after`/`start_before`, same
  NYC-local-date → aware-datetime conversion as `tools._local_date`)
- results as a table: date/time (NYC local), title (links to
  `/event/{id}`), venue, neighborhood, borough, price, tags, flags
  (`low_confidence`, `possibly_cancelled`), and the event's upstream `url` /
  `venue_map_url` as links
- `limit` from a dropdown, default 50, hard cap 200 (browsers scan fine at
  200; the 50 cap in `tools.py` is a token budget, which doesn't apply here)
- all params are GET query params, so a filtered view is a bookmarkable URL

Validation mirrors `tools.py`: bad dates / end-before-start render an error
message in the page (HTTP 400), never a traceback.

## DB access (the part with real gotchas)

- New `db.connect_events_ro(path)` — `sqlite3.connect(f"file:{path}?mode=ro",
  uri=True)` + `row_factory`. **Never call `init_events` from the dashboard**
  (it's DDL; the whole point is this process cannot write). If the DB or its
  tables don't exist yet, render a clear "no database yet — has ingest run?"
  page instead of crashing.
- **WAL read-only gotcha:** SQLite can open a WAL database read-only only if
  the `-shm`/`-wal` sidecars already exist *or* the process can create them —
  a reader needs write access to the `-shm` file in the general case. Since
  the server and ingest (same host dir, both rw) keep those sidecars alive
  in practice, `mode=ro` works — but **do not mount `./data` as `:ro`** in
  the dashboard service; enforcement lives in the connection string, not the
  mount. If a hardened `:ro` mount is ever wanted, that requires
  `immutable=1`, which is wrong for a live-updating DB — don't.
- Per-request connection open/close (same pattern as `tools.py`); WAL means
  readers never block the nightly ingest and vice versa.
- Read-only-ness is enforced twice: `mode=ro` at the SQLite layer, GET-only
  routes at the HTTP layer.

## Deployment

New service in `docker-compose.yml` (same image, so one build pipeline):

```yaml
  nyc-events-dashboard:
    image: ghcr.io/yesfinethankyou/nyc-kids-mcp:latest
    command: ["python", "-m", "nyc_events.dashboard"]
    restart: unless-stopped
    environment:
      DB_PATH: /data/events.db
      DASHBOARD_PORT: "8766"
    volumes:
      - ./data:/data          # rw mount; ro is enforced at the connection (WAL gotcha above)
    ports:
      - "127.0.0.1:8766:8766"
    labels:
      com.centurylinklabs.watchtower.enable: "true"
    # healthcheck: TCP probe on 8766, same recipe as nyc-events
```

Notes for the implementer:

- The dashboard needs no `.env` secrets — **do not** add `env_file: .env`.
  It must run without `MCP_AUTH_TOKEN` ever entering its environment.
- Bind `127.0.0.1` like the main service — never LAN-exposed.
- No `FORWARDED_ALLOW_IPS` / proxy-header handling needed: there's no OAuth
  discovery to get scheme-right, and no per-IP rate limiter (tailnet-only).
- Same non-root uid 10001; the dashboard writes nothing, so the hardening
  is free.
- `docker-compose.dev.yml` gets the matching override (build + uid 1000)
  if local testing is wanted.

**Host exposure — `tailscale serve`, NOT `funnel`:**

```bash
tailscale serve --bg --https=8766 http://127.0.0.1:8766
```

`serve` publishes to tailnet members only; `funnel` is the public-internet
command. The existing Funnel config for 8765 is untouched. Document in the
README, right next to the Funnel instructions, that the dashboard port must
never be funneled — that's the entire security model. (Optional check while
setting up: `tailscale funnel status` should still list only 8765.)

## Testing

`tests/test_dashboard.py`, no network, temp DB seeded via `init_events` +
`upsert_events` (existing fixture pattern):

- `db.source_health` numbers (counts, staleness math, zero-row registered
  source appears) — in `test_db.py` alongside the other db query tests.
- Starlette `TestClient` over the app: `/` renders every enabled source;
  `/events` filter params thread through to results; bad date input → 400
  page; unknown event id → 404; `event_id` round-trips to detail.
- **XSS guard:** seed an event titled `<script>alert(1)</script>` and assert
  the rendered pages contain the escaped form only. Event data is scraped
  from the public web — treat every field as attacker-influenced.
- **Read-only guard:** assert the app has no non-GET routes, and that
  `connect_events_ro` raises `sqlite3.OperationalError` on a write attempt.
- Missing-DB path renders the friendly page, not a 500.

## Explicitly out of scope

- Any authentication/session code (tailnet is the boundary; if that ever
  changes, revisit the whole design rather than bolting on a login form).
- Any write path: no editing, hiding, or re-tagging events; no triggering
  ingest from the browser. If "kick off an ingest" is ever wanted, that's a
  host-side concern (DSM Task Scheduler), not a web button.
- `oauth.db` anywhere near this process — no token/user visibility in v1.
  (A "connected users" panel would be read-only too, but it puts the auth DB
  in a second process's hands; decide separately if wanted.)
- Exposing the dashboard on the Funnel hostname in any form.
- JS frameworks, build steps, CDN assets.

## Open decisions (settle at implementation time)

1. **`ingest_runs` log table (recommended follow-up, not required for v1).**
   Today "ingest health" is inferred from `MAX(last_seen)` — a proxy that
   can't distinguish "source returned 0 rows" from "ingest never ran", and
   exit codes 2/3 vanish into the cron log. A small append-only table
   (`source`, `started_at`, `finished_at`, `rows_upserted`, `status`)
   written by `ingest.main` would make the dashboard's health column
   first-class and give `/ingest-health` real data too. It's the only part
   of this plan that touches ingest code, which is why it's severable: v1
   ships on `last_seen` alone.
2. **Port** — 8766 assumed throughout; any free port works, change compose +
   `tailscale serve` together.
3. **Auto-refresh** — a `<meta http-equiv="refresh" content="300">` on `/`
   is probably worth it for a wall-tablet view; trivial either way.

## Effort estimate

- `dashboard.py` (app, three pages, rendering helpers): ~250–350 lines
- `db.py` additions (`connect_events_ro`, `source_health`): ~60 lines
- compose + README (serve command, "never funnel this" warning): small
- tests: ~150–200 lines

No migrations (unless open decision 1 is taken — that one is a plain
`CREATE TABLE IF NOT EXISTS` in `EVENTS_SCHEMA`, same precedent as
`geocode_cache`).
