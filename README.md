# To start on your local machine

```
cp .env.example .env # edit this file for your config
uv sync
uv run uvicorn api.main:app
```