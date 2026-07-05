#!/bin/bash
set -e

echo "=== Harness Initialization: nyc-kids-mcp ==="

# This repo ships a committed virtualenv at .venv (Python >=3.11). All project
# commands run through it — see CLAUDE.md "Commands". If .venv is missing
# (fresh clone in a new environment), create it and install the package with
# its dev extras before running verification.
PY=".venv/bin/python"
RUFF=".venv/bin/ruff"

if [ ! -x "$PY" ]; then
  echo "=== .venv not found — creating and installing (editable + dev extras) ==="
  python3 -m venv .venv
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -e ".[dev]"
fi

echo "=== Running test suite (should always be green) ==="
"$PY" -m pytest tests/ -q

echo "=== Lint ==="
"$RUFF" check

echo "=== Verification Complete ==="
echo ""
echo "Next steps:"
echo "1. Read CLAUDE.md (project guide; '## Phase roadmap' is the canonical feature state)."
echo "2. Review session-handoff.md for where the last session left off."
echo "3. Pick ONE unfinished feature to work on; implement only that feature."
echo "4. Re-run this script (or 'pytest tests/ -q' + 'ruff check') before claiming done."
echo ""
echo "Other useful commands (see CLAUDE.md):"
echo "  $PY -m nyc_events.ingest      # one-shot ingest from ENABLED_SOURCES"
echo "  $PY -m nyc_events.server      # run HTTP MCP server (needs MCP_AUTH_TOKEN)"
echo "  $PY -m nyc_events.seed_fake   # populate fake events for connector smoke-testing"
