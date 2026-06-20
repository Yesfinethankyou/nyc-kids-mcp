---
name: ingest-health
description: Run a dry ingest and flag event sources that returned zero rows or look stale — the symptom of a scraper broken by an upstream HTML change. Use after a deploy, when you suspect a source went quiet, or as a periodic check. Reuses the existing ingest CLI output and db.list_sources; does not reimplement either.
---

# ingest-health

Detect sources that have silently gone bad. Scrapers break when upstream HTML
changes (CLAUDE.md calls this out as expected), and the failure is quiet — the
nightly ingest just stops adding rows for that source. This skill runs the
ingest locally and cross-checks freshness so a dead source surfaces immediately.

## What it runs

This skill runs against the **local dev environment** (`.venv` + `data/events.db`).
It runs the real ingest, which **writes to the dev DB**. If you want the dev DB
left pristine, run this against a throwaway DB first — e.g. a fresh
`data/events.db` (delete it and let ingest recreate it) or after
`.venv/bin/python -m nyc_events.seed_fake`. The production equivalent of step 1
is `docker exec nyc-events python -m nyc_events.ingest` on the NAS; use that
form only when you explicitly want to check prod.

## Procedure

### 1. Run the ingest

```bash
.venv/bin/python -m nyc_events.ingest
```

This already prints, per source, a line like
`mommy_poppins: 12 inserted, 5 updated (233 fetched)`, then a `TOTAL:` line, and
on failure a `FAILED SOURCES:` block (and exits non-zero). Lean on that output —
do not reimplement the loop. (See `src/nyc_events/ingest.py:37-45`.)

### 2. Flag from the run output

- **FAILED** — any source listed under `FAILED SOURCES:` raised during
  `fetch()`. Surface the exception text verbatim; this is almost always an
  upstream change or a network/selector break.
- **EMPTY** — any source whose line shows `(0 fetched)`. The scraper ran but
  matched nothing — selectors are probably stale.
- **MISSING** — cross-check the lines printed against `ENABLED_SOURCES` in
  `src/nyc_events/sources/__init__.py`. A source enabled in the registry that
  produced *no line at all* (neither a count line nor a FAILED entry) is also a
  problem — catch it, don't assume a missing line means healthy.

### 3. Read freshness from the DB

Use the project's own `db.list_sources` (same shape as
`src/nyc_events/db.py:349-362`) rather than a raw SQL string:

```bash
.venv/bin/python -c "
from nyc_events import db
import os
with db.connect_events(os.environ.get('DB_PATH', 'data/events.db')) as c:
    for r in db.list_sources(c):
        print(r)
"
```

Each row has `source`, `event_count`, `earliest_event`, `latest_event`,
`last_seen`. Flag:

- **STALE (no upcoming)** — `latest_event` is in the past. The source has only
  events that have already happened; nothing new is coming in.
- **STALE (not refreshed)** — `last_seen` is older than this run's ingest
  timestamp. The source's rows weren't touched this run, so it didn't actually
  return current data even if old rows linger.

### 4. Report

Print one row per enabled source: name, fetched-this-run, total in DB, latest
upcoming event date, last_seen, and a verdict — **OK / EMPTY / FAILED / STALE /
MISSING**. **Lead with anything not OK**, then list the healthy ones. If
everything is OK, say so in one line.

## Out of scope

- Don't edit source files or "fix" a broken scraper here — hand a flagged
  source to the `source-fixer` agent. This skill only diagnoses and reports.
- Don't touch `models.py`, `db.py`, or `server.py`.
