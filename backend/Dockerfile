# =============================================================================
# NEX Studio Backend — Node.js 20 + Claude CLI + Python 3.12
# Multi-stage build: deps → runtime
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir poetry==1.8.5

COPY pyproject.toml poetry.lock ./
RUN poetry export --without dev -f requirements.txt -o requirements.txt \
    && pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: Runtime — Python + Node.js 20 + Claude CLI
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies + Node.js 20 via NodeSource
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI globally
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# Copy Python site-packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Create non-root user matching the host andros uid (1000).
# Claude CLI refuses to run as root — same pattern as NEX Command backend.
RUN useradd -m -u 1000 -s /bin/bash andros \
    && mkdir -p /home/andros/.claude /app/uploads \
    && chown -R andros:andros /app /home/andros

# Copy application code with correct ownership
COPY --chown=andros:andros backend/ ./backend/
COPY --chown=andros:andros alembic.ini ./alembic.ini
COPY --chown=andros:andros migrations/ ./migrations/

USER andros

EXPOSE 9176

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -m backend.scripts.healthcheck

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9176"]
