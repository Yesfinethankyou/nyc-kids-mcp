#!/bin/bash
set -euo pipefail

# PreToolUse guard for Bash commands. Enforces CLAUDE.md's
# "Files that must never be committed" rule deterministically: blocks any
# `git add` / `git commit` that would stage data/*.db*, .env, or .venv/.
#
# These are gitignored, so the realistic ways they slip in are `git add -f`
# (force bypasses .gitignore) or an explicit path. CLAUDE.md says: "If you ever
# see one of these proposed for git add, stop and ask." This hook is that stop.
#
# Reads the PreToolUse JSON payload on stdin, exits 2 to block (stderr is fed
# back to the model), or exits 0 to allow.

payload="$(cat)"

python3 - "$payload" <<'PY'
import json, re, sys

try:
    data = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(0)  # not JSON we understand — don't interfere

cmd = (data.get("tool_input") or {}).get("command", "")
if not cmd:
    sys.exit(0)

# Only `git add` can introduce a never-commit file: these paths are gitignored,
# so `git commit <path>` of an untracked one just errors, and `git commit -a`
# only re-commits already-tracked files. So scope to `git add`.
if not re.search(r"\bgit\s+add\b", cmd):
    sys.exit(0)

# Strip -m / --message argument text so a commit message that merely *mentions*
# .env / data/*.db (e.g. "guard git add of .env") isn't matched as if it were a
# staged path. Best-effort: handles the common quoted forms.
scan = re.sub(r"(-m|--message)\s+(\"[^\"]*\"|'[^']*'|\S+)", " ", cmd)

reasons = []

# .env secrets file — but .env.example / .env.sample / .env.template are tracked
# templates and must stay allowed.
if re.search(r"(?<![\w.])\.env\b(?!\.(example|sample|template))", scan):
    reasons.append(".env (secrets file)")

# The virtualenv.
if re.search(r"(?<![\w.])\.venv\b", scan):
    reasons.append(".venv/ (virtualenv)")

# SQLite databases (events.db / oauth.db plus -wal / -shm sidecars).
if re.search(r"\.db(-wal|-shm)?\b", scan) or re.search(r"\bdata/[^\s]*\.db", scan):
    reasons.append("data/*.db* (SQLite databases)")

# Force-add bypasses .gitignore for everything — always worth a human check.
if re.search(r"\bgit\s+add\b[^|&;]*\s(-f\b|--force\b)", scan):
    reasons.append("git add --force (bypasses .gitignore)")

if reasons:
    sys.stderr.write(
        "BLOCKED by guard-commit hook — this command would stage files that "
        "CLAUDE.md says must never be committed:\n  - "
        + "\n  - ".join(reasons)
        + "\n\nPer CLAUDE.md ('Files that must never be committed'): stop and "
        "ask the user before staging these. If this is a genuine exception, "
        "explain why and let the user run the git command themselves.\n"
    )
    sys.exit(2)

sys.exit(0)
PY
