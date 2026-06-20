# Session Handoff

## What was done (last two sessions)

### Session: Issues #4 / #5 / #6 (merged in PR #19)

- [x] **Issue #4 — FTS5 VACUUM footgun (doc fix):** CLAUDE.md "DB migrations"
      section now warns never to run `VACUUM` on `events.db` without
      immediately rebuilding the FTS5 index. The `events` table has a TEXT
      primary key, so SQLite may renumber implicit rowids on VACUUM and silently
      desynchronize the external-content FTS5 index.
- [x] **Issue #5 — Split consent password from master bearer:** Added optional
      `MCP_CONSENT_PASSWORD` env var. `/authorize` POST checks it first, falling
      back to `MCP_AUTH_TOKEN`. With it set, the browser consent form never
      touches the master bearer; the two credentials rotate independently.
      `.env.example` and CLAUDE.md updated.
- [x] **Issue #6 — Hygiene grab-bag:** OAuth DB churn reduced (5-min in-memory
      token cache in `BearerAuthMiddleware`); rate-limiter buckets now evict
      when empty; tool args clamped (`limit ≤ 50`, `days_ahead ≤ 365`);
      `UTC = UTC` no-ops removed; `import json` moved to module level;
      Green-Wood `_strip_html` now uses `html.unescape()`.

### Session: Agents / skills / hooks review (this PR)

- [x] **Agent fixes:** `source-verifier` had Industry City / Domino Park /
      Governors Island listed as REJECTED "no structured surface" precedents —
      all three are live sources; each rejection was a non-impersonating-probe
      artifact. Corrected to Time Out NY Kids. Added explicit warning never to
      reject on a plain (non-impersonating) probe.
- [x] **New fast-paths in both agents:** Sanity GROQ API (Domino Park precedent)
      and Craft CMS / Solspace Calendar JSON (Governors Island precedent) added
      to `source-adder`'s "Platform fast paths" and `source-verifier`'s
      classify step.
- [x] **`source-fixer` agent:** New agent owns repair of broken EXISTING
      scrapers — the workflow that fell between `source-adder` (new sources only)
      and the diagnose-only `ingest-health` skill. `ingest-health` now hands
      off to `source-fixer`.
- [x] **`guard-commit` PreToolUse hook:** Blocks `git add` of `.env`,
      `.venv/`, and `data/*.db*`, plus `git add --force`. Scoped to `git add`
      only; strips `-m` message text to avoid false positives. Verified with
      13 true/false-positive cases.
- [x] **`db-maintenance` skill:** Safe VACUUM + mandatory FTS rebuild procedure.
      Wraps the issue #4 VACUUM footgun with backup, before/after baseline,
      rebuild, and `PRAGMA integrity_check`. SQL validated against a seeded DB.
- [x] **CI workflow (`test.yml`):** `pytest` + `ruff` on every PR and push to
      main. Added to main manually (OAuth app in this environment lacks
      `workflow` scope for git push).
- [x] **Lint fix:** Stray double blank line in `tests/test_security_fixes.py`
      left by the issues session's `UTC = UTC` removal.

## Current state

Suite: **405 passed**, ruff: **clean**. All changes on
`claude/review-open-issues-bzeumn`, PR open against main.

## Decisions made

- **5-min OAuth token cache TTL.** A revoked token stays valid up to 5 minutes.
  To revoke immediately: restart the server.
- **Rate-limiter eviction on drain**, not on creation.
- **`MCP_CONSENT_PASSWORD` falls back to `MCP_AUTH_TOKEN`.** Additive only;
  single-var deployments need no changes.
- **`source-fixer` is a separate agent**, not a widened `source-adder`. Keeps
  each agent's scope narrow and description accurate.

## Blockers / risks

- **`guard-commit` hook** is active this session: any `git add` whose command
  text contains `.env`, `.venv`, or `data/*.db` is blocked. If unexpectedly
  blocked, check the hook — it strips `-m` messages to avoid false positives.
- **OAuth token cache** means a revoked token (row deleted from `oauth.db`)
  stays valid for up to 5 min in a running server.

## Next session startup

1. Read `CLAUDE.md` (project guide — hard-won quirks, security baseline).
2. Read `progress.md` for current feature state.
3. Run `pytest tests/ -q` + `ruff check` — suite should be green.

## Recommended next steps

- Merge the open PR (agents/skills/hooks improvements).
- If `MCP_CONSENT_PASSWORD` is desirable on the NAS, generate a second token
  and add it to `.env` before the next connector approval.
- Phase 3 remains queued (`PHASE-3-PLAN.md`): geocoding / distance is the
  high-value first step.
- Filter consolidation pass (`FILTER-REVIEW.md`) still pending maintainer
  review — no filters were changed in either session.
