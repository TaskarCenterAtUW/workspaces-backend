#!/usr/bin/env bash
# Auto-fix the formatting issues that scripts/ci.sh checks for.
#
# Runs isort then black in write mode over the repo (the same tools/config CI
# enforces, minus the `--check`). Safe to run repeatedly; it only rewrites files
# that are not already compliant. Does not touch pyright — type errors are not
# auto-fixable.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Sorting imports (isort)"
uv run isort .

echo ""
echo "==> Formatting code (black)"
uv run black .

echo ""
echo "Done. Review the changes with 'git diff', then re-run ./scripts/ci.sh."
