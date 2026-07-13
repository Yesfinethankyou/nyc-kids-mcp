## Summary

<!-- What changed, and why. If this picks up after an earlier PR merged
     mid-branch, say so. -->

## Changes

<!-- One item per logical change. Call out anything a reviewer wouldn't
     expect from the title: a quirk you had to work around, a filter
     strategy, an id-scheme decision, a doc claim that turned out stale. -->

-

## Test plan

- [ ] `.venv/bin/python -m pytest tests/ -q` passes
- [ ] `.venv/bin/ruff check` passes
- [ ] Live-verified where it matters — a dashboard/UI change driven in a
      browser (screenshot or described), or a new/changed source dry-run
      against the real upstream. N/A for docs-only or pure refactors.

## Docs

- [ ] `session-handoff.md` updated (PR creation is blocked until this is
      touched for the branch — see `.claude/hooks/require-handoff-update.sh`)
- [ ] `CLAUDE.md` updated if this changes a cross-cutting invariant, adds or
      removes a source, or touches the HTTP security baseline
- [ ] `SOURCES-BACKLOG.md` updated if this builds, rejects, or re-probes a
      candidate source (as-built notes or a rejection + revisit condition)
- [ ] `README.md` updated if this changes the shipped source list, event
      counts, or setup/deploy steps

## Security surface

- [ ] This PR does not touch `auth.py` / `oauth.py` / `users.py`. If it
      does, that's flagged above and the reason is explicit — a diff that
      touches both the security surface and something else (a new tool, a
      new source) is a red flag per `CLAUDE.md`.
