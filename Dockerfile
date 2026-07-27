FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The Create-Project scaffolding + the backend's git ops shell out to git AND gh: init.sh does
# git init/add/commit (needs git); Stage-4 push_and_verify runs `gh auth setup-git` then `git push`
# (needs gh — it wires the HTTPS credential helper from the GH_TOKEN/GITHUB_TOKEN env). python:3.12-slim
# ships neither, so project creation failed at `git init` (exit 127, v4.0.36) then at the push. Install
# both. gh authenticates non-interactively from GH_TOKEN at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# git needs a committer identity or `git commit` fails ("unable to auto-detect email address") — the
# scaffolding init.sh commits the initial project, and the backend commits on the operator's behalf
# (dirty-tree guard). safe.directory=* so the root-run backend can operate on host-owned project
# checkouts under /opt/projects without git's "dubious ownership" refusal (v4.0.37).
RUN git config --global user.email "studio@isnex.eu" \
    && git config --global user.name "NEX Studio" \
    && git config --global init.defaultBranch main \
    && git config --global --add safe.directory '*'

# The backend orchestrates docker via the mounted /var/run/docker.sock: `docker compose build/up/down`
# for the pipeline build + Verifikácia smoke, UAT/PROD deploy (uat_provisioner/orchestrator), and
# `docker run` for per-project CI-runner provisioning. python:3.12-slim has no docker CLI, so those
# shell-outs raised FileNotFoundError — which aborted even Create Project at the CI-runner step (v4.0.40).
# Install the docker CLI + compose plugin (client only; the daemon is the host's, via the socket).
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Node.js — the AI Agent (claude) shells out to node/npm when it builds a generated app's frontend
# (npm install / build), so the backend that hosts the agent needs a node runtime. The `claude` binary
# itself is the proven host build, mounted read-only from /home/andros/.local at runtime (compose), so
# the deployed agent is byte-identical to the one the team uses — no version drift. (v4.0.41)
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# The agent argv (build_claude_argv) invokes the BARE `claude` command, so it must resolve on PATH.
# claude is mounted at /home/andros/.local/bin (off PATH) at runtime, so symlink it onto PATH. The
# target resolves at exec time (when the mount is present). (v4.0.42)
RUN ln -sf /home/andros/.local/bin/claude /usr/local/bin/claude

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
# Create-Project scaffolding reads charter/CI/smoke templates from /app/templates (agent-shared-base.md,
# ai-agent-charter.md, auditor-charter.md, release_smoke_test.sh, github-actions-workflow.yml, uat/, …).
# Without this, v2 charter provisioning fails "shared base template missing" and create 500s (v4.0.39).
COPY templates/ ./templates/

# Migrations run at container start, not by hand. See docker-entrypoint.sh for why: no deploy path
# applied them, so a schema change shipped only if someone remembered an undocumented step — and
# forgetting it surfaced as a 500 on one screen, not as a failed deploy. (CR-V2-063 post-mortem.)
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 9176

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -m backend.scripts.healthcheck

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9176"]
