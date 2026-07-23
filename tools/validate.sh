#!/usr/bin/env bash
# Local pre-push config validation. Runs the canonical validator from a sibling amnesia
# checkout (single source of truth — AMS-430). No schemas or validator are vendored here.
set -euo pipefail

AMNESIA_DIR="${AMNESIA_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/amnesia}"
VALIDATOR="$AMNESIA_DIR/.github/actions/validate-config/validate.py"

if [[ ! -f "$VALIDATOR" ]]; then
  echo "error: amnesia validator not found at $VALIDATOR" >&2
  echo "       clone haremedia/amnesia as a sibling of this repo, or set AMNESIA_DIR=/path/to/amnesia" >&2
  exit 2
fi

if ! python3 -c "import yaml, jsonschema" 2>/dev/null; then
  echo "error: python3 is missing pyyaml/jsonschema. Install them:" >&2
  echo "       pip install -r \"$(dirname "${BASH_SOURCE[0]}")/requirements.txt\"" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$VALIDATOR" "$REPO_ROOT"
