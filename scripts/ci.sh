#!/usr/bin/env bash
# Run the CI checks locally, mirroring .github/workflows/ci.yml.
#
# Runs every check by default; a failure in one step does not stop the others,
# and the script exits non-zero if any step failed. Pass --fail-fast to stop at
# the first failure instead.
set -uo pipefail

cd "$(dirname "$0")/.."

fail_fast=0
[[ "${1:-}" == "--fail-fast" ]] && fail_fast=1

failed=()

run() {
  local name="$1"; shift
  echo ""
  echo "==> ${name}"
  if "$@"; then
    echo "--- ${name}: OK"
  else
    echo "--- ${name}: FAILED"
    failed+=("${name}")
    [[ "${fail_fast}" == 1 ]] && summary_and_exit
  fi
}

summary_and_exit() {
  echo ""
  if [[ ${#failed[@]} -eq 0 ]]; then
    echo "All CI checks passed."
    exit 0
  fi
  echo "CI checks FAILED: ${failed[*]}"
  exit 1
}

run "Install the project"            uv sync --all-extras
run "Check imports with isort"       uv run isort --check-only --diff .
run "Check code formatting (black)"  uv run black --check .
run "Type-check with pyright"        uvx pyright --pythonpath .venv/bin/python api tests
run "Run tests"                      uv run pytest tests

summary_and_exit
