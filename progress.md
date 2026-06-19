# Session Progress Log

## Current State

**Last Updated:** 2026-06-19
**Active Feature:** none — Phase 1–2 shipped; Phase 3 (feat-006, feat-008–012) planned, not started

> **Canonical phase status:** CLAUDE.md `## Phase roadmap` is the source of
> truth. This log and `feature-list.json` are derived snapshots — when a phase
> ships, update CLAUDE.md first, then reconcile these to match.

## Status

### What's Done

- [x] feat-001 — Phase 1 NYC permit ingest core (model, compute_id, split SQLite + FTS5, `nyc_permitted_events`)
- [x] feat-002 — MCP server + OAuth 2.1/PKCE shim + Checkpoint C HTTP security baseline
- [x] feat-003 — Missing-event detection (possible-cancellation flagging, four guard layers)
- [x] feat-004 — Phase 2 editorial scrapers (8 sources live; buildable backlog cleared)
- [x] feat-005 — Docker packaging + deploy (Checkpoint D: multi-arch GHCR image, non-root, Funnel)
- [x] feat-007 — Session-handoff harness tooling (init.sh + feature-list.json + progress.md + session-handoff.md; merged via PR #13)

### What's In Progress

- Nothing in flight. Phase 3 is planned but not started.

### What's Next

Phase 3, in the PHASE-3-PLAN.md sequencing order (now broken out as separate
features in `feature-list.json`):

1. feat-006 — enrichment pipeline scaffold + caching layer (foundational).
2. feat-008 — tech-debt bundle #4/#5/#6 (server-touching; do alongside #1).
3. feat-009 — geocoding + neighborhood + distance-from-home.
4. feat-010 — indoor/outdoor heuristic flag.
5. feat-011 — weather on outdoor events (needs feat-009 + feat-010).
6. feat-012 — venue expansion (Workstream B; Playwright fallback only if a probe needs it).

## Blockers / Risks

- [ ] Risk: `data/*.db*`, `.env`, and `.venv/` must never be committed
  (gitignored). Stop and ask if any are proposed for `git add`.
- [ ] Risk: Phase 3 Playwright adoption adds ~300–450 MB to the image — keep it
  in a separate ingest image so the always-on server stays lean/hardened
  (PHASE-3-PLAN.md).

## Decisions Made

- **Harness files tailored, not generic**: `init.sh` runs `.venv/bin/python -m
  pytest tests/ -q` + `.venv/bin/ruff check` instead of the template's bare
  `python -m pytest` / `compileall`, matching CLAUDE.md "Commands".
  - Context: this repo ships a committed `.venv` and gates all commands through it.
- **session-handoff.md points at CLAUDE.md, not AGENTS.md**: this project's
  guide lives in CLAUDE.md; there is no AGENTS.md.

## Files Modified This Session

- `init.sh` — repo-specific verification (venv pytest + ruff) and next-steps.
- `feature-list.json` — real Phase 1–3 feature roadmap with status + evidence.
- `progress.md` — this log.
- `session-handoff.md` — populated handoff for the next session.

## Evidence of Completion

- [x] Tests pass: `.venv/bin/python -m pytest tests/ -q` → `332 passed in 2.36s`
- [x] Lint clean: `.venv/bin/ruff check` → `All checks passed!`
- [ ] Manual verification: n/a — docs/tooling change only.

## Notes for Next Session

Phases 1, 2 and Checkpoint C/D are done and the suite is green. The only open
implementation track is Phase 3 — now broken into feat-006 (scaffold) and
feat-008–012, all not-started; read PHASE-3-PLAN.md before touching any of
them. `server.py` is a single big module; if
it grows past ~600 lines, split the OAuth handlers out first (CLAUDE.md). New
sources follow the source-adder recipe in `.claude/agents/source-adder.md` and
must add a real-response fixture under `tests/fixtures/`.
