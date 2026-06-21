#!/usr/bin/env bash
# PreToolUse hook (matcher: mcp__github__create_pull_request).
#
# Project rule: the session handoff doc must be brought up to date with each PR.
# This blocks PR creation unless session-handoff.md has actually been touched for
# the current branch — staged/unstaged, changed vs origin/main, or in the latest
# commit. Once the handoff is updated + committed, the PR tool is allowed.
#
# Fail-open: any git error or unexpected state allows the PR (a config hiccup
# must never permanently wedge PR creation); only the clear "handoff untouched"
# case denies.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

dirty=$(git status --porcelain -- session-handoff.md 2>/dev/null)
branch=$(git diff --name-only origin/main...HEAD -- session-handoff.md 2>/dev/null)
last=$(git diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null | grep -x 'session-handoff.md')

if [ -n "${dirty}${branch}${last}" ]; then
  exit 0  # handoff updated for this branch — allow the PR
fi

cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Project rule: update session-handoff.md to reflect this session's work and commit it BEFORE opening the PR. Update + commit the handoff, then create the PR again."}}
JSON
exit 0
