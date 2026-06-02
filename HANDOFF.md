# Handoff: Mommy Poppins Phase 2 Source — Complete

## What just shipped (commit `8d26d78`)

First Phase 2 editorial source: **Mommy Poppins NYC**. Sitemap-based URL
discovery + detail page scraping. Yields ~233 NYC family events per run with
real descriptions, URLs, age ranges, coordinates, and prices — a major quality
upgrade over Phase 1's permit-only `low_confidence` data.

### Files added/changed

- `src/nyc_events/sources/mommy_poppins.py` — full implementation (was a stub)
- `src/nyc_events/sources/__init__.py` — wired into `ENABLED_SOURCES`
- `pyproject.toml` — added `curl_cffi>=0.9` dependency
- `tests/test_mommy_poppins_parse.py` — 47 tests covering all parser helpers
- `tests/fixtures/mommy_poppins_detail.html` — real event page fixture
- `tests/fixtures/mommy_poppins_detail_no_date.html` — edge case fixture
- `tests/fixtures/mommy_poppins_sitemap_page.xml` — synthetic sitemap fixture

### Key architectural decisions

1. **curl_cffi instead of httpx** for fetching. Cloudflare TLS-fingerprints
   Python 3.11 + OpenSSL 3.0.x and returns 403. `curl_cffi` with
   `impersonate='chrome'` bypasses this. The `_fetch_page` method is the only
   place it's used — all other HTTP in the project still uses httpx.

2. **JSON-LD + drupalSettings extraction.** Mommy Poppins runs Drupal 8/9.
   JSON-LD is wrapped in `{"@context": ..., "@graph": [...]}`. DrupalSettings
   is a raw JSON `<script>` tag (not jQuery.extend like Drupal 7). Control
   characters (`\x03` in `pluralDelimiter`) are sanitized before parsing.

3. **List venue names.** Some JSON-LD `location.name` values are lists
   (e.g., `['David Geffen Hall', 'LeFrak Lobby']`). These are joined with
   `", "` before passing to the Event model.

4. **Borough inference** from coordinate bounding boxes + venue name lookup
   table. No geocoding API needed.

5. **External ID = Drupal node ID** (`path.currentPath = "node/48231"`),
   with fallback to URL slug. Stable IDs hash on `source|id:external_id`.

### Test suite

136 tests total (89 existing + 47 new), all passing. Lint has pre-existing
warnings in `db.py`, `server.py`, and older test files — none in the new
mommy_poppins code.

## What's next (Phase 2 remaining sources)

Per `CLAUDE.md` Phase roadmap, these sources are planned but not started:

- **Brooklyn Public Library (BPL)** — likely iCal or API
- **Time Out NY Kids** — scraper
- **Brooklyn Children's Museum** — scraper
- **Prospect Park Alliance** — scraper

Each follows the same recipe: fixture capture, parser module in
`src/nyc_events/sources/`, registry wiring in `__init__.py`, parser tests.
See `.claude/agents/source-adder.md` for the established pattern. Use the
`source-adder` agent type for these.

## Known issues / tech debt

- Pre-existing ruff lint warnings (27 total) in `db.py`, `server.py`,
  `ingest.py`, `models.py`, older tests. Not related to this change.
- `httpx` is still a top-level dependency (used by `server.py` and
  `nyc_permitted_events.py`). `curl_cffi` is only used by mommy_poppins.
- No retry logic on mommy_poppins fetches — a transient 5xx on one page
  skips that event silently. Acceptable at this scale.

## Container deployment

- GHCR workflow auto-builds from main. Watchtower on the NAS pulls `:latest`.
- The Dockerfile's builder stage already has `build-essential`, which
  curl_cffi needs to compile. No Dockerfile changes were needed.
- Ingest is a `docker exec` cron: `sudo docker exec nyc-events python -m nyc_events.ingest`

## Constraints to remember

- Only the user runs `sudo` / `docker` commands.
- `server.py` serves MCP at root (`/`), not `/mcp`.
- Don't commit `data/*.db*`, `.env`, or `.venv/`.
