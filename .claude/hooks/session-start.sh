#!/bin/bash
set -euo pipefail

# Bootstrap .venv for Claude Code on the web — fresh containers have no
# venv, so the documented test/lint commands fail until this runs.
# Local dev manages its own venv; skip outside remote sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Idempotent: skip if the venv already has the package + dev deps.
if [ -x .venv/bin/python ] && .venv/bin/python -c "import pytest, ruff, nyc_events" 2>/dev/null; then
  exit 0
fi

python3 -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
