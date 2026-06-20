# Session Handoff

## Current Objective

- Goal: Close the three open GitHub issues (#4, #5, #6) from the June 10
  architecture review.
- Current status: All three closed and merged into a PR (#19) on branch
  `claude/review-open-issues-bzeumn`. Suite green (405 passed).

## Completed This Session

- [x] **Issue #4 — FTS5 VACUUM footgun (doc fix):** Added warning to CLAUDE.md
      "DB migrations" section: never run `VACUUM` on `events.db` without
      immediately rebuilding the FTS5 index (`INSERT INTO events_fts(events_fts)
      VALUES('rebuild')`). The `events` table has a TEXT primary key (no
      `INTEGER PRIMARY KEY` alias), so SQLite may renumber implicit rowids on
      VACUUM, silently desynchronizing the external-content FTS5 index.
- [x] **Issue #5 — Split consent password from master bearer:** Added optional
      `MCP_CONSENT_PASSWORD` env var. `/authorize` POST now checks it first,
      falling back to `MCP_AUTH_TOKEN` when unset. With it set, the browser
      consent form never touches the master bearer; the two credentials rotate
      independently. Zero migration cost. `.env.example` and CLAUDE.md OAuth
      model section updated.
- [x] **Issue #6 — Grab-bag hygiene (all four items):**
      - **OAuth DB churn:** `BearerAuthMiddleware` now checks a 5-minute
        in-memory `_oauth_token_cache` dict before opening `oauth.db`. Lazy
        eviction when the cache exceeds 200 entries.
      - **Rate limiter eviction:** `_rate_limit` now deletes `_rate_state` keys
        when buckets drain empty during the sliding-window sweep, preventing
        monotonic memory growth.
      - **Unclamped tool args:** `limit` clamped to ≤ 50, `days_ahead` to
        ≤ 365 across all three listing tools. Docstrings updated.
      - **Nits:** `UTC = UTC` no-ops removed from `server.py` and
        `test_security_fixes.py`; `import json` moved to module level; Green-Wood
        `_strip_html` now uses `html.unescape()` instead of three hand-rolled
        entity substitutions.
- [x] GitHub issues #4, #5, #6 closed as completed.
- [x] PR #19 opened.

## Verification Evidence

| Check | Command | Result |
|---|---|---|
| Tests | `.venv/bin/python -m pytest tests/ -q` | 405 passed |
| Lint | `.venv/bin/ruff check` | clean |

## Files Changed (this branch)

- `CLAUDE.md` — VACUUM warning in "DB migrations"; `MCP_CONSENT_PASSWORD` in
  OAuth model section.
- `src/nyc_events/server.py` — OAuth token cache, rate limiter eviction,
  tool arg clamping, `UTC = UTC` removed, `json` import moved, `MCP_CONSENT_PASSWORD`
  support in `authorize_post`.
- `src/nyc_events/sources/greenwood_cemetery.py` — `html.unescape()` in
  `_strip_html`.
- `tests/test_security_fixes.py` — `UTC = UTC` no-op removed.
- `.env.example` — `MCP_CONSENT_PASSWORD` documented.

## Decisions Made

- **5-minute OAuth token cache TTL.** Acceptable revocation lag at personal
  scale (single user, single connector). A deleted token stays valid for up
  to 5 minutes. To revoke immediately: restart the server.
- **Rate limiter eviction on drain, not on creation.** Buckets are only
  removed when the sweep empties them, not when they're first created — avoids
  churn on IPs that are actively bursting.
- **`MCP_CONSENT_PASSWORD` falls back to `MCP_AUTH_TOKEN`.** Single-var
  deployments require no changes; the new var is purely additive.

## Blockers / Risks

- Standing rule: never `git add` `data/*.db*`, `.env`, or `.venv/` (gitignored).
- OAuth token cache means a revoked token (row deleted from `oauth.db`) stays
  valid for up to 5 minutes in a running server. Only relevant if actively
  revoking a compromise mid-session; server restart clears the cache immediately.

## Next Session Startup

1. Read `CLAUDE.md` (project guide — hard-won quirks, security baseline).
2. Read `progress.md` for current feature state.
3. Run `pytest tests/ -q` + `ruff check` before editing — suite should be green.

## Recommended Next Step

- Merge PR #19.
- If `MCP_CONSENT_PASSWORD` is desirable, generate a second token and add it
  to the NAS `.env` before the next connector approval.
- Phase 3 remains queued (`PHASE-3-PLAN.md`): geocoding/distance is the
  high-value first step.
- Filter consolidation pass (`FILTER-REVIEW.md`) is still pending maintainer
  review — no filters were changed in this session.
