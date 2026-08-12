# Patent Reviewer Backend

## Layout

Application code is in the `app/` directory.

```
app
├── __main__.py # FastAPI app, and routes
├── models.py # DB models
├── schemas.py # Schema objects
├── data.py # Seed data
└── db.py # Database utils
```

## First-time setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```sh
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

Make sure you create a .env file (see .env.example) with the OpenAI API key we've provided.

## Running locally

To run the backend locally, with auto-reload on code changes,

```sh
uv run uvicorn app.__main__:app --reload
```

## DB

On start-up, the app will initialise an in-memory SQLite DB, and fill it with some seed data. If you decide that you want to reset your changes, all you need to do is re-run the backend.
