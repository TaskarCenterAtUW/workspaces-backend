# AGENTS.md

Working agreement for coding agents in this repo.

`CLAUDE.md` is the primary reference — permission model, the OSM proxy layer,
the two databases, the token bridge, and the testing conventions. Read it first.
This file covers only the mechanics of finishing a change.

## Always run the CI checks before reporting work as done

```
./scripts/ci.sh
```

This is the same set CI runs, in the same order: `uv sync`, isort, black,
pyright, pytest. It runs every step even after one fails and exits non-zero if
any did, so a single run lists all the problems rather than the first one.

Flags:

* `--fail-fast` — stop at the first failing step.
* `--integration` — additionally run `pytest -m integration`, the
  testcontainers/PostGIS suite. Needs a running Docker daemon. CI passes this;
  locally it is usually unnecessary (~1 min).

To auto-fix the formatting half:

```
./scripts/fix-lint.sh   # isort + black in write mode; does not touch pyright
```

**Do not report a change as complete on the strength of `pytest` alone.**
Passing tests with a failing `black --check` or `pyright` is a red build. Both
have broken CI here after the tests were green.

## Two failure modes that keep recurring

**Let black wrap lines; don't hand-format.** A line you wrote just inside the
88-column limit often exceeds it after an edit, and hand-wrapping rarely matches
what black wants. Run `black` (or `fix-lint.sh`) and commit its output.

**Narrow `Optional` before subscripting.** Pyright's `reportOptionalSubscript`
fires on things like `HTTPException.headers`, which is `dict | None`:

```python
# error: Object of type "None" is not subscriptable
assert excinfo.value.headers["WWW-Authenticate"].startswith("Basic")

# fine
assert excinfo.value.headers is not None
assert excinfo.value.headers["WWW-Authenticate"].startswith("Basic")
```

This is a genuine `Optional`, so narrow it — do **not** silence it with a
`pyright: ignore`. Inline ignores are reserved for the SQLModel false positives
described in `CLAUDE.md`, and even then they must be targeted to the specific
line and rule.

Keep `api/` and `tests/` at zero pyright errors.

## When adding behavior

Add matching `# @test:` lines to the module's outline and tests to cover them —
see "`@test:` comment outlines" in `CLAUDE.md`. The outlines are the spec; when
they and the code disagree, treat the outline as authoritative and fix the code.
