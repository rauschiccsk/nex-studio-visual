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
# git is required by template_bootstrap subprocess (icc-claude-template
# init.sh runs `git init` + first commit when bootstrapping new projects).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
        git \
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

# Set system-wide git identity used by template_bootstrap when init.sh
# does the first commit on a freshly-created project. The identity is
# generic ICC service-account style; per-project commits later will be
# attributed via the developer's own git config from the host or from
# the project's local .git/config (init.sh creates the repo with this
# identity then the developer overrides as needed).
RUN git config --system user.email "ci@icc.sk" \
    && git config --system user.name "ICC NEX Studio Bot"

# Install Claude CLI globally
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# Copy Python site-packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Create non-root user matching the host andros uid (1000).
# Claude CLI refuses to run as root — same pattern as NEX Command backend.
RUN useradd -m -u 1000 -s /bin/bash andros \
    && mkdir -p /home/andros/.claude /home/andros/.local/bin /app/uploads /var/lib/nex-studio/terminal-logs \
    && ln -sf /usr/bin/claude /home/andros/.local/bin/claude \
    && chown -R andros:andros /app /home/andros /var/lib/nex-studio
# Symlink ~/.local/bin/claude → /usr/bin/claude: the host's
# /home/andros/.claude/.claude.json has installMethod=native (claude
# CLI was natively installed on ANDROS host into ~/.local/bin/claude).
# That config is mounted into the container; without the symlink,
# claude CLI 2.1.126+ emits a transient red startup warning
# "Method is native, but claude command not found at /home/andros/.local/bin/claude".
# DISABLE_AUTOUPDATER=1 silences auto-update warnings but NOT this
# method check (separate pre-flight). Director directive 2026-05-19:
# fix even cosmetic warnings, no loose ends.

# Copy application code with correct ownership
COPY --chown=andros:andros backend/ ./backend/
COPY --chown=andros:andros alembic.ini ./alembic.ini
COPY --chown=andros:andros migrations/ ./migrations/

USER andros

EXPOSE 9176

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -m backend.scripts.healthcheck

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9176"]
