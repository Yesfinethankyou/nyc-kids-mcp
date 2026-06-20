# Session Handoff

## Current Objective

- Goal: Session-tracker + docs upkeep and a new venue source — keep the
  roadmap/docs honest and add Industry City.
- Current status: All work merged except the session-tracker PR. Suite green
  (356 passed). Phase 3 is planned but not started.
- Branch / PR: `claude/feature-list-phase3` → PR #17 (this branch; tracker
  files only). Everything else merged to `main` (PRs #13–#16).

## Completed This Session

- [x] Harness tooling tailored to the repo (`init.sh`, `feature-list.json`,
      `progress.md`, `session-handoff.md`) — PR #13 (merged).
- [x] Faster nightly ingest: reordered `ENABLED_SOURCES` cheapest-first with
      `mommy_poppins` last, and cut its per-page delay 1.5s → 0.5s — PR #14
      (merged). Root cause was a strictly-sequential loop with no per-source
      time budget; mommy_poppins (~700 pages) starved everything after it.
- [x] Aligned phase status across README + CLAUDE.md: Phase 2 → done,
      Phase 3 → planned (was "speculative") — PR #15 (merged).
- [x] Industry City source built (WordPress + Tribe REST API) + Domino Park /
      Governors Island flagged NEEDS RE-PROBE — PR #16 (merged).
- [x] Session tracker: feat-007 done, feat-006 split into Phase-3 features
      (feat-006/008–012), feat-004 bumped to nine live sources — PR #17 (open).

## Verification Evidence

| Check | Command | Result | Notes |
|---|---|---|---|
| Tests | `.venv/bin/python -m pytest tests/ -q` | 356 passed | full suite |
| Lint | `.venv/bin/ruff check` | All checks passed! | clean |
| Ingest order | `python -c "from nyc_events.sources import ENABLED_SOURCES; ..."` | mommy_poppins last | fast sources first |

## Files Changed (this branch / PR #17)

- `feature-list.json` — feat-007 done; feat-006 split into feat-006/008–012;
  feat-004 = nine live sources.
- `progress.md` — reconciled to match.
- `session-handoff.md` — this file.

## Decisions Made

- **Probe correctly, or false-reject.** Industry City's "headless CMS, no
  wp-json" verdict was a bot-block artifact — a `curl_cffi`
  (`impersonate="chrome"`) re-probe found a plain Tribe REST API. Domino Park
  and Governors Island were rejected by the same flawed probe and are now
  flagged NEEDS RE-PROBE rather than trusted.
- **Industry City filtering is keyword-driven** (categories aren't kid-curated):
  allowlist on title/description, `Nightlife` hard-exclude, adult/alcohol
  blocklist. Dropped the fragile `"no strollers"` / `"children under the age"`
  blocklist entries (they catch legit kid events); kept only `"no children"`.
  Net: the outdoor World Cup watch parties are kept (13 kept on a live 60-day
  fetch).
- **CLAUDE.md `## Phase roadmap` is the canonical status source**;
  `feature-list.json` / `progress.md` are derived snapshots that defer to it.

## Blockers / Risks

- Standing rule: never `git add` `data/*.db*`, `.env`, or `.venv/` (gitignored).
- Phase 3 Playwright adoption adds ~300–450 MB to the image — keep it in a
  separate ingest image so the always-on server stays lean (PHASE-3-PLAN.md).

## Next Session Startup

1. Read `CLAUDE.md` (project guide — hard-won quirks, security baseline).
2. Read `feature-list.json` and `progress.md` for current feature state.
3. Review this handoff.
4. Run `./init.sh` (or `pytest tests/ -q` + `ruff check`) before editing —
   the suite should always be green.

## Recommended Next Step

- Merge PR #17 if not already in.
- Two open tracks, both planned in `PHASE-3-PLAN.md`:
  1. **Re-probe Domino Park + Governors Island** with `curl_cffi` — their
     rejections are now suspect and both have heavy family programming.
  2. **Phase 3** in sequencing order: feat-006 (enrichment scaffold +
     caching) → feat-008 (tech debt #4/#5/#6) → feat-009 (geocode +
     distance) → feat-010 (indoor/outdoor) → feat-011 (weather) →
     feat-012 (venue expansion). Geocoding is the high-value first step.
