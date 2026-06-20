# Session Handoff

## Current Objective

- Goal: Re-probe and build the two suspect "no feed" venue sources (Governors
  Island, Domino Park), and stand up a filter-review tech-debt item for the
  maintainer to action personally.
- Current status: Both sources built, enabled, and green. Filter inventory
  compiled in `FILTER-REVIEW.md` for a human review pass (no filter changes
  made). Suite green (405 passed).
- Branch / PR: `claude/jolly-mccarthy-w53ajo` (pushed; no PR opened yet).

## Completed This Session

- [x] **Governors Island source** (`governors_island`) — re-probed with
      `curl_cffi`; prior "no API surface" verdict was a non-impersonating-probe
      artifact. Custom Craft CMS / Solspace-Calendar JSON feed at
      `/things-to-do.json` (NOT WordPress/Tribe). Inclusive + blocklist. Dates
      are floating local wall-time mislabeled `Z` → parsed as America/New_York.
      Opted OUT of missing-detection (feed caps at 100 rows, id-asc). 100 rows
      → 85 kept. 22 parser tests.
- [x] **Domino Park source** (`domino_park`) — re-probed; "Sanity headless, no
      feed" verdict was also a probe artifact. Public Sanity GROQ API (project
      `4shd8slw`, anonymous reads). **`variant` is the authoritative recurrence
      switch, not `frequency`**: `reoccurring` docs expand per-occurrence;
      `single-day`/`multi-day` are one event each (their leftover frequency is
      vestigial). Opted INTO missing-detection (full GROQ re-fetch). 125 docs →
      104 events / 60-day window. 26 parser tests.
- [x] **Filter-review tech debt** — added the "review filter lists for all
      sources" item and compiled `FILTER-REVIEW.md` (per-source inclusion gates
      + lists + flagged inconsistencies). Maintainer is reviewing personally;
      no filters changed.
- [x] Docs updated: `CLAUDE.md` (roadmap → both live; hygiene section points to
      `FILTER-REVIEW.md`), `SOURCES-BACKLOG.md` (both BUILT blocks + the
      resolved "non-impersonating probe" lesson + tech-debt note), `README.md`
      (shipped lists, layout tree).

## Verification Evidence

| Check | Command | Result | Notes |
|---|---|---|---|
| Tests | `.venv/bin/python -m pytest tests/ -q` | 405 passed | full suite |
| Lint | `.venv/bin/ruff check` | All checks passed! | clean |
| GovIsland dry-run | `list(GovernorsIslandSource().fetch())` | 85 events | 100 rows → 15 dropped |
| Domino dry-run | `list(DominoParkSource().fetch())` | 104 events | 125 docs, 60-day window |
| Registry | `python -c "from nyc_events.sources import ENABLED_SOURCES; ..."` | 11 sources | both new sources wired |

## Files Changed (this branch)

- `src/nyc_events/sources/governors_island.py` — new source.
- `src/nyc_events/sources/domino_park.py` — new source.
- `src/nyc_events/sources/__init__.py` — both wired into `ENABLED_SOURCES`.
- `tests/test_governors_island_parse.py`, `tests/test_domino_park_parse.py` — new.
- `tests/fixtures/governors_island_sample.json`, `tests/fixtures/domino_park_sample.json` — new.
- `tests/test_missing_detection.py` — Governors Island excluded from
  missing-detection; opt-in count 8 → 9 (Domino added).
- `FILTER-REVIEW.md` — new filter inventory.
- `CLAUDE.md`, `SOURCES-BACKLOG.md`, `README.md` — doc updates.

## Decisions Made

- **`variant` over `frequency` for Domino recurrence.** Upstream stores some
  recurring series as one `reoccurring` doc and others as several `single-day`
  docs carrying vestigial (sometimes negative-span) `frequency`/`endDate`.
  Trusting `frequency` would double-count and emit garbage dates; trusting
  `variant` matches the upstream's own rendering intent.
- **Missing-detection opt-in is per-feed, not per-source-type.** Domino's GROQ
  query is a true full re-fetch → opted in. Governors Island's feed hard-caps
  at 100 rows ordered id-asc (newer events scroll off) → opted out, same
  caution as `mommy_poppins`.
- **Filters are not consolidated yet — by request.** The maintainer wants to
  review them personally; `FILTER-REVIEW.md` is the inventory + flagged issues,
  and nothing was changed pre-emptively.
- **Always probe with `curl_cffi` impersonation.** Three sources (Industry
  City, Governors Island, Domino Park) were each falsely rejected by a
  non-impersonating probe that ate a 403. Lesson recorded in `SOURCES-BACKLOG.md`.

## Blockers / Risks

- Standing rule: never `git add` `data/*.db*`, `.env`, or `.venv/` (gitignored).
- `FILTER-REVIEW.md` is a point-in-time extract — re-run its introspection
  snippet before acting; source code is authoritative.

## Next Session Startup

1. Read `CLAUDE.md` (project guide — hard-won quirks, security baseline).
2. Read `feature-list.json` and `progress.md` for current feature state.
3. Review this handoff and `FILTER-REVIEW.md`.
4. Run `pytest tests/ -q` + `ruff check` before editing — suite should be green.

## Recommended Next Step

- **Maintainer:** do the filter review using `FILTER-REVIEW.md`; hand back the
  decisions (canonical blocklist, where it lives, which tag keywords to
  word-boundary) and a follow-up session can apply them as one pass.
- Open a PR for this branch if desired (none opened yet).
- Phase 3 remains queued (`PHASE-3-PLAN.md`): geocoding/distance is the
  high-value first step. Domino Park already ships lat/lng, so it's ready for
  the distance filter.
