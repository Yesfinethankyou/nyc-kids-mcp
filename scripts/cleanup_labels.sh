#!/usr/bin/env bash
# One-time cleanup after the 2026-07-07 label-taxonomy standardization.
# Not part of the app; run locally once, then delete this file.
#
# Requires: gh CLI, authenticated with write access (`gh auth status`).
#
# 1. Deletes labels superseded by the new type:/priority:/status:/area:
#    taxonomy (now unused by any open issue) plus a verification artifact.
# 2. Sets color + description on the new taxonomy labels — they exist and
#    are applied to issues already, but were auto-created via the API with
#    a flat default color and no description (no label-admin endpoint was
#    available to set these at creation time).
#
# Safe to re-run: deletions of already-gone labels are swallowed, edits just
# overwrite color/description with the same values.

set -euo pipefail

REPO="yesfinethankyou/nyc-kids-mcp"

echo "== Deleting superseded/orphaned labels =="
for label in \
  bug \
  P0 \
  P1 \
  P2 \
  data-quality \
  security \
  enhancement \
  type:test-probe
do
  gh label delete "$label" --repo "$REPO" --yes \
    && echo "  deleted: $label" \
    || echo "  skip (not found): $label"
done

echo
echo "== Setting colors + descriptions on the new taxonomy labels =="

gh label edit "type:bug"          --repo "$REPO" --color d73a4a --description "Code does something other than intended"
gh label edit "type:data-quality" --repo "$REPO" --color e99695 --description "Data produced is wrong/misleading, not a logic error"
gh label edit "type:security"     --repo "$REPO" --color 8B0000 --description "Auth/OAuth/rate-limiting/injection/secrets"
gh label edit "type:enhancement"  --repo "$REPO" --color a2eeef --description "New capability or deliberate improvement"
gh label edit "type:chore"        --repo "$REPO" --color c5def5 --description "Refactor/housekeeping, no behavior change"

gh label edit "priority:P0" --repo "$REPO" --color b60205 --description "Incorrect output/crash/corruption, normal operation"
gh label edit "priority:P1" --repo "$REPO" --color d93f0b --description "Incorrect behavior, common edge cases"
gh label edit "priority:P2" --repo "$REPO" --color fbca04 --description "Incorrect behavior, rare edge cases"
gh label edit "priority:P3" --repo "$REPO" --color c2e0c6 --description "Minor/cosmetic"

gh label edit "status:triage"      --repo "$REPO" --color ededed --description "Not yet verified/reproduced"
gh label edit "status:ready"       --repo "$REPO" --color 0e8a16 --description "Verified, scoped, safe to start"
gh label edit "status:in-progress" --repo "$REPO" --color 1d76db --description "Actively worked, pair with a PR"
gh label edit "status:blocked"     --repo "$REPO" --color 24292e --description "External dependency - comment must explain what unblocks it"

gh label edit "area:auth"    --repo "$REPO" --color 5319e7 --description "auth.py/oauth.py/users.py"
gh label edit "area:sources" --repo "$REPO" --color 7057ff --description "Any scraper module"
gh label edit "area:db"      --repo "$REPO" --color 8a63d2 --description "db.py, schema, migrations, FTS"
gh label edit "area:ingest"  --repo "$REPO" --color a586e0 --description "ingest.py/enrich.py, telemetry"
gh label edit "area:tools"   --repo "$REPO" --color bfa8ea --description "tools.py, MCP surface"
gh label edit "area:infra"   --repo "$REPO" --color d4c5f9 --description "Dockerfile, compose, CI"

echo
echo "Done. Run 'gh label list --repo $REPO' to verify."
