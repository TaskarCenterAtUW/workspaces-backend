#!/usr/bin/env bash
# Run the CI checks locally, mirroring .github/workflows/ci.yml.
#
# Runs every check by default; a failure in one step does not stop the others,
# and the script exits non-zero if any step failed.
#
# Flags:
#   --fail-fast     stop at the first failing step instead of running all
#   --integration   also run the integration suite (`pytest -m integration`),
#                   which boots a real PostGIS database via testcontainers and
#                   therefore needs a running Docker daemon
set -uo pipefail

cd "$(dirname "$0")/.."

fail_fast=0
integration=0
for arg in "$@"; do
  case "${arg}" in
    --fail-fast)   fail_fast=1 ;;
    --integration) integration=1 ;;
    *) echo "Unknown option: ${arg}" >&2; exit 2 ;;
  esac
done

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

# --cov-append so this run adds to the coverage data from "Run tests" instead
# of erasing it; otherwise the final .coverage would describe only the
# integration subset. Coverage is a union of executed lines, so the tests that
# run in both passes are simply counted once.
if [[ "${integration}" == 1 ]]; then
  run "Run integration tests"        uv run pytest tests -m integration --cov-append
fi

summary_and_exit
