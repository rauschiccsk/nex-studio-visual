FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# git is required at runtime: the Create-Project scaffolding (init.sh: git init/add/commit) and the
# backend's own git operations (dirty-tree guard, commit, discard) shell out to it. python:3.12-slim
# ships without git, so project creation failed with "git: command not found" (exit 127) — v4.0.36.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install poetry and export requirements
RUN pip install --no-cache-dir poetry==1.8.5

COPY pyproject.toml poetry.lock ./
RUN poetry export --without dev -f requirements.txt -o requirements.txt \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y poetry

# Copy application code
COPY backend/ ./backend/
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
# The Aktualizácie changelog is served from the per-version RELEASE_NOTES.md files
# (backend.services.release_notes reads /app/docs/specs/versions/v*/RELEASE_NOTES.md).
# Without this the endpoint returns [] and the Aktualizácie tab is empty (v4.0.33).
COPY docs/specs/versions/ ./docs/specs/versions/

EXPOSE 9176

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -m backend.scripts.healthcheck

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9176"]
