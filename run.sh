#!/usr/bin/env bash
# Run Thesis2Lib with the project-local Conda Python.
# Usage: ./run.sh [app.py arguments]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/.conda/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: Conda environment not found at $ROOT/.conda" >&2
  echo "Create it from the application root with:" >&2
  echo "  ./setup_conda.sh" >&2
  exit 1
fi

exec "$PYTHON" "$ROOT/app.py" "$@"
