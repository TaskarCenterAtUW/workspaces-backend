#!/usr/bin/env python3
"""Render the CI test + coverage summary.

Prints a test-result tally and a per-file coverage table to stdout (so the
step log carries it) and, when running under GitHub Actions, appends the same
content as Markdown to ``$GITHUB_STEP_SUMMARY`` so it renders on the run's
summary page.

This is the Python counterpart to the frontend's ``scripts/coverage-report.mjs``.
The frontend gets its test tally free from Vitest's built-in GitHub Actions
reporter; pytest has no equivalent, so the tally here is parsed from the JUnit
XML that ``scripts/ci.sh`` asks pytest to write.

Inputs, all optional -- a missing one degrades to omitting that section rather
than failing, because this runs with ``if: always()`` and must not turn a green
build red:
  reports/junit-*.xml   pytest JUnit output (one per pytest invocation)
  .coverage             coverage.py data file
"""

import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
COVERAGE_DATA = ROOT / ".coverage"


def _run_coverage(*args: str) -> str | None:
    """Return `coverage <args>` stdout, or None if the call fails."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "coverage", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def test_report() -> list[str]:
    """Markdown lines tallying test files and test cases, or [] if unavailable.

    Counts are summed across every JUnit file. A single pytest run emits one
    <testsuite>; the suite is run twice in CI (the second time filtered to
    `-m integration`), so the tallies are per-invocation, matching what the
    step log shows.
    """
    files = sorted(REPORTS.glob("junit-*.xml")) if REPORTS.is_dir() else []
    if not files:
        return []

    suite_files: set[str] = set()
    total = failed = skipped = 0
    for path in files:
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError:
            continue
        # pytest writes <testsuites><testsuite>; older versions wrote a bare
        # <testsuite>. iter() handles both.
        for suite in root.iter("testsuite"):
            total += int(suite.get("tests", 0))
            failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
            skipped += int(suite.get("skipped", 0))
        for case in root.iter("testcase"):
            name = case.get("file") or case.get("classname")
            if name:
                suite_files.add(name)

    if not total:
        return []

    passed = total - failed - skipped
    mark = "❌" if failed else "✅"
    lines = [
        "## Test report",
        "",
        f"- **Test Files:** {len(suite_files)} total",
        f"- **Test Results:** {mark} {passed} passed"
        + (f" · {failed} failed" if failed else "")
        + (f" · {skipped} skipped" if skipped else "")
        + f" · {total} total",
        "",
    ]
    return lines


def coverage_table() -> list[str]:
    """Markdown lines for the per-file coverage table, or [] if unavailable."""
    if not COVERAGE_DATA.exists():
        return []
    table = _run_coverage("report", "--format=markdown")
    if not table:
        return []
    total = _run_coverage("report", "--format=total") or "?"
    return [f"## Test coverage — {total}%", "", table, ""]


def main() -> int:
    lines = test_report() + coverage_table()
    if not lines:
        lines = ["## Test summary", "", "_No test or coverage data was produced._"]

    body = "\n".join(lines)
    print(body)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(body + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
