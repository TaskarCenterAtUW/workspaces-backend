# Workspaces Backend

## What this does

This is a combination API backend for workspaces, providing /workspaces* methods, as well as a proxy to 
the OSM API ("openstreetmap website") plus OSM CGI-map (the C-accelerated methods) that enforces 
authorization and authentication based on a TDEI/Keycloak JWT token (see main.py for this proxy logic). 

## Branch Index

* ```develop``` do your work here
* ```development``` keep this up to date with the "development" environment / dev tag
* ```staging``` keep this up to date with the "staging" environment / stage tag
* ```production``` keep this up to date with the "production" environment / prod tag
  
## To start on your local machine for dev work

```
cp .env.example .env # edit this file for your config
uv sync
uv run uvicorn api.main:app
```

## Running the tests

Tests are fast and require no database, Docker, or network (see
`tests/README.md` for the design, and `CLAUDE.md` for conventions).

```
uv run pytest                 # full suite with coverage (configured in pyproject.toml)
uv run pytest --no-cov -q     # quick run, no coverage
uv run pytest tests/unit      # unit tests only
uv run pytest tests/integration  # integration tests only
uv run pytest -k workspaces   # filter by keyword
```

Type-check and format (matches the pre-commit hooks):

```
uvx pyright --pythonpath .venv/bin/python api tests
uv run black api tests && uv run isort api tests
```

