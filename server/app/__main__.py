# Kept so `uvicorn app.__main__:app` (Dockerfile, both READMEs, CLAUDE.md) works.
from app.main import app

__all__ = ["app"]
