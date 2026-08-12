# Use an official Python runtime as a parent image
FROM python:3.13-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Install uv (pinned — :latest makes the build non-reproducible)
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/src/app/.venv \
    PYTHONUNBUFFERED=1

# Copy dependency files first for caching
COPY pyproject.toml uv.lock ./

# Dependency layer. --no-install-project, or every source edit would invalidate it.
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY . .

# Project layer
RUN uv sync --frozen --no-dev

# Make port 8000 available to the world outside this container
EXPOSE 8000

# --no-sync: `uv run` otherwise re-verifies the environment on every start, and with the
#   source bind mount that re-verification tries to rebuild into the mounted directory.
# --reload-dir app: the SQLite file lives in the bind-mounted data/, and the default
#   watcher would restart the server on every database write.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.__main__:app", \
     "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "app"]
