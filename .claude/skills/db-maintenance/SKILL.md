---
name: db-maintenance
description: Safely VACUUM or run maintenance on data/events.db without desyncing the FTS5 full-text index. Use when asked to compact/shrink/vacuum the events database, after a large prune, or when search results look wrong and you suspect index corruption. Always pairs VACUUM with the mandatory events_fts rebuild and a before/after sanity check.
---

# db-maintenance

`data/events.db` carries a **silent, severe footgun** documented in CLAUDE.md
(`## DB migrations`). The `events` table has a TEXT primary key — there is no
`INTEGER PRIMARY KEY` alias — so SQLite may **renumber its implicit rowids on
VACUUM**. `events_fts` is an external-content FTS5 table keyed on those rowids
(`content='events'`, `content_rowid='rowid'`). After a renumber the full-text
index silently desynchronizes: `search_events` returns the *wrong rows* with no
error. This skill makes VACUUM and related maintenance safe.

**Scope:** `data/events.db` only. `data/oauth.db` has no FTS and is not touched
here. Never cross-reference the two (CLAUDE.md `## DB migrations`).

## Golden rule

> **Never `VACUUM` events.db without immediately rebuilding `events_fts`.**
> The rebuild is `INSERT INTO events_fts(events_fts) VALUES('rebuild');`

## Procedure

### 1. Stop writers

VACUUM takes an exclusive lock. Make sure nothing else is writing — no nightly
ingest mid-run, and on the NAS the server holds only reads but a concurrent
`docker exec ... ingest` will block or fail. Run this during a quiet window.

### 2. Back up first (cheap insurance)

```bash
cp data/events.db "data/events.db.bak.$(date +%Y%m%d%H%M%S)"
```

Do **not** `git add` the backup — it matches the `data/*.db*` never-commit rule
(the guard-commit hook will block it). Delete it once you've confirmed success.

### 3. Capture a before-baseline

Use the project's own connection helper so PRAGMAs/migrations match production.
Record the row count and a known-good FTS query result to compare against after:

```bash
.venv/bin/python - <<'PY'
import os
from nyc_events import db
with db.connect_events(os.environ.get("DB_PATH", "data/events.db")) as c:
    rows = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    # A term that should reliably match something in the catalog.
    fts = c.execute(
        "SELECT COUNT(*) FROM events e JOIN events_fts f ON f.rowid = e.rowid "
        "WHERE events_fts MATCH ?", ("family",)
    ).fetchone()[0]
    print(f"BEFORE: events={rows}  fts_match('family')={fts}")
PY
```

### 4. VACUUM + rebuild in one shot

Both statements together — never VACUUM alone:

```bash
.venv/bin/python - <<'PY'
import os
from nyc_events import db
with db.connect_events(os.environ.get("DB_PATH", "data/events.db")) as c:
    c.execute("VACUUM")
    c.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")
    c.commit()
    print("VACUUM + FTS rebuild done")
PY
```

### 5. Verify the index is back in sync

Re-run the baseline query. `events` count must be unchanged and the FTS match
count must be **the same or higher** (rebuild can only correct a prior
desync, never drop real matches):

```bash
.venv/bin/python - <<'PY'
import os
from nyc_events import db
with db.connect_events(os.environ.get("DB_PATH", "data/events.db")) as c:
    rows = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    fts = c.execute(
        "SELECT COUNT(*) FROM events e JOIN events_fts f ON f.rowid = e.rowid "
        "WHERE events_fts MATCH ?", ("family",)
    ).fetchone()[0]
    integ = c.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"AFTER:  events={rows}  fts_match('family')={fts}  integrity={integ}")
PY
```

`integrity_check` must print `ok`. If the AFTER event count differs from BEFORE,
or `fts_match` dropped, **stop and restore the backup** — something is wrong.

### 6. Clean up

Once AFTER looks right, remove the backup:

```bash
rm data/events.db.bak.*
```

## Repairing a suspected-desync without a full VACUUM

If search is returning wrong rows but you have not VACUUMed (or don't want to),
the rebuild alone fixes a desynced index — it's idempotent and safe to run any
time:

```bash
.venv/bin/python -c "
import os
from nyc_events import db
with db.connect_events(os.environ.get('DB_PATH','data/events.db')) as c:
    c.execute(\"INSERT INTO events_fts(events_fts) VALUES('rebuild')\"); c.commit()
print('FTS rebuilt')
"
```

## Out of scope

- Schema changes / migrations — those live in `db.py`'s `_migrate_*` (CLAUDE.md
  `## DB migrations`). Don't add a migration framework here.
- `data/oauth.db` — no FTS, no VACUUM footgun; leave it alone.
- Don't edit `models.py`, `db.py`, or `server.py` from this skill.
