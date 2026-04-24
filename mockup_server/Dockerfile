# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Intentionally minimal: no ORM, no backend package. Raw pg8000 +
# aiohttp serve two SELECTs' worth of schema coupling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY mockup_server/requirements.txt ./mockup_server/requirements.txt
RUN pip install --no-cache-dir -r mockup_server/requirements.txt

COPY mockup_server ./mockup_server

# Silently defaults to 9190 — overridable via MOCKUP_ADMIN_PORT.
EXPOSE 9190

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -fsS "http://localhost:${MOCKUP_ADMIN_PORT:-9190}/admin/health" || exit 1

CMD ["python", "-m", "mockup_server.app"]
