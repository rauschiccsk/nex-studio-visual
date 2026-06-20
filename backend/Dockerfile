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
# Stage 1b: Release notes — isolate ONLY the per-version RELEASE_NOTES.md
# files (the "Aktualizácie" changelog, served by GET /api/v1/release-notes).
#
# A flat `COPY docs/specs/versions/*/RELEASE_NOTES.md ./...` would collapse
# every match into a single dir by basename and collide (Docker copies
# multiple file sources by basename) — losing the v<X>/ parent the service
# reads as the version. Copying the tree here then pruning everything except
# RELEASE_NOTES.md preserves that structure. Crucially, the rest of
# docs/specs (development-spec, customer-dialogue, F-xxx internal dev docs —
# ~960K) lives ONLY in this throwaway stage and never reaches the runtime
# image: the runtime COPY --from below takes just the cleaned subset.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS release-notes

WORKDIR /notes
COPY docs/specs/versions/ ./docs/specs/versions/
# Drop every file that is not a RELEASE_NOTES.md, then prune the now-empty
# version dirs. `-mindepth 1` keeps the `versions/` root itself even when NO
# RELEASE_NOTES.md exists yet, so the runtime `COPY --from` below never fails
# on a missing source (it just copies an empty dir → endpoint returns []).
RUN find docs/specs/versions -type f ! -name 'RELEASE_NOTES.md' -delete \
    && find docs/specs/versions -mindepth 1 -type d -empty -delete

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

# CR-NS-002 — Docker CLI + GitHub CLI pre autonomous Implementer lifecycle.
# Implementer (cez AgentTerminal subprocess) potrebuje `docker` + `gh` CLI
# binárky aby mohol orchestrovať cross-project deploys (UAT compose) a
# monitorovať CI runs cez gh API. `docker.sock` je už mounted v compose
# (Director directive 2026-05-19), ale chýba CLI binary.
#
# Docker CLI: install z official Docker repo (`docker-ce-cli` — len client,
# žiadny daemon — daemon beží na host cez mounted socket). Debian default
# `docker.io` package na slim base produkuje silent install fail (§9.1
# NEX Inbox v0.1.0 lesson: binárky chýbajú napriek "successful" install) —
# fixed v CI run 26745805192 fix-commit.
#
# GitHub CLI: install z official GitHub Apt repo (nie v default Debian).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg lsb-release \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin gh \
    && rm -rf /var/lib/apt/lists/*

# Verify binárky existujú (per §9.1 build pattern lesson z NEX Inbox v0.1.0 —
# silent install fail by inak prešiel až do runtime crash). Path-agnostic
# cez `command -v` lebo `docker-ce-cli` môže inštalovať do /usr/bin alebo
# /usr/local/bin per Debian convention.
RUN docker_bin="$(command -v docker)" && test -x "$docker_bin" \
    && gh_bin="$(command -v gh)" && test -x "$gh_bin" \
    && docker compose version \
    && echo "Docker CLI at $docker_bin (+ compose plugin), GitHub CLI at $gh_bin"

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
    && groupadd -g 110 docker \
    && usermod -aG docker andros \
    && chown -R andros:andros /app /home/andros /var/lib/nex-studio
# Docker group with GID 110 matching ANDROS host (`getent group docker`
# → docker:x:110:andros). Combined with the /var/run/docker.sock bind
# mount in docker-compose.yml (Director directive 2026-05-19, voľba A),
# this gives AG agents inside the BE container access to the host Docker
# daemon — needed for testcontainers-python (real Postgres integration
# tests per L-012, real Stalwart Mail server for IMAP integration, etc).
#
# SECURITY NOTE: Docker socket access = effective root on host (any
# user with docker access can mount /, spawn privileged containers,
# etc). Acceptable for ICC internal dev environment (single trusted
# Director); MUST NOT be enabled if NEX Studio is ever exposed to
# untrusted users. See ICC_STANDARDS for the trust boundary policy.
# Symlink ~/.local/bin/claude → /usr/bin/claude: the host's
# /home/andros/.claude/.claude.json has installMethod=native (claude
# CLI was natively installed on ANDROS host into ~/.local/bin/claude).
# That config is mounted into the container; without the symlink,
# claude CLI 2.1.126+ emits a transient red startup warning
# "Method is native, but claude command not found at /home/andros/.local/bin/claude".
# DISABLE_AUTOUPDATER=1 silences auto-update warnings but NOT this
# method check (separate pre-flight). Director directive 2026-05-19:
# fix even cosmetic warnings, no loose ends.

# Put ~/.local/bin first in PATH so claude CLI's "Native installation
# exists but ~/.local/bin is not in your PATH" warning is silenced.
# Both paths point to the same binary via the symlink above; PATH
# precedence is cosmetic — the BE invokes claude via subprocess with
# the explicit /usr/bin/claude path so functional behavior is unchanged.
ENV PATH="/home/andros/.local/bin:${PATH}"

# Copy application code with correct ownership
COPY --chown=andros:andros backend/ ./backend/
COPY --chown=andros:andros alembic.ini ./alembic.ini
COPY --chown=andros:andros migrations/ ./migrations/

# Bake ONLY the per-version RELEASE_NOTES.md files (the "Aktualizácie"
# changelog) from the pruned stage above — NEVER the full docs/specs tree.
# Read by backend.services.release_notes via GET /api/v1/release-notes.
COPY --from=release-notes --chown=andros:andros /notes/docs/specs/versions/ ./docs/specs/versions/

USER andros

EXPOSE 9176

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -m backend.scripts.healthcheck

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9176"]
