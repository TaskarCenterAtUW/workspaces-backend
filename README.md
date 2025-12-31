# Workspaces Backend

## What this does

This is a combination API backend for workspaces, providing /workspaces* methods, as well as a proxy to 
the OSM API ("openstreetmap website") plus OSM CGI-map (the C-accelerated methods) that enforces 
authorization and authentication based on a TDEI/Keycloak JWT token (see main.py for this proxy logic). 

## To start on your local machine for dev work

```
cp .env.example .env # edit this file for your config
uv sync
uv run uvicorn api.main:app
```

