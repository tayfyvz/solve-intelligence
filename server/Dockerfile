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

# THE PRODUCTION COMMAND. One image, two commands: the development one (--reload) is a
# `command:` override in docker-compose.yml, next to the bind mount that makes it
# necessary. It is not a branch in Python and not a second Dockerfile.
#
# --no-sync: `uv run` otherwise re-verifies the environment on every start, and with the
#   compose bind mount that re-verification tries to rebuild into the mounted directory.
# app.main:app, NOT app.__main__:app: __main__.py exists only as a re-export shim for the
#   inherited scaffold's documented command, and importing a module named __main__ as a
#   library is a trap worth not shipping. The shim stays, so nobody's muscle memory breaks.
# --workers 1, deliberately, and NOT the 4 a production image would normally want: four
#   processes writing one SQLite file is contention, not concurrency. Raising this is
#   step 7 of the Postgres move (PLAN §28.2.6) and is not safe before it.
# --proxy-headers: the deployment in §28.2.2 puts nginx in front, so X-Forwarded-* is
#   what carries the real scheme and client address.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
