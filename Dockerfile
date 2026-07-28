# =============================================================================
# NEX Studio Visual — backend image. THE ONLY BACKEND RECIPE.
#
# There used to be two: this one and `backend/Dockerfile`. Everything that
# actually ships is built from THIS file — CI (`docker build … .`, ci.yml job
# "Build Docker Images") and the deployed PROD image alike — while
# `backend/Dockerfile` was referenced only by the dev compose and a handful of
# non-compose call sites (scripts + docs) — never by anything that ships. So fixes
# written into that file silently never reached production: it baked
# `scripts/notify_telegram.sh` into the image, and `/app/scripts` did not exist
# in the running v4.0.76 PROD container. A second recipe is not redundancy, it
# is a decoy that quietly absorbs fixes; it has been deleted and its genuine
# contents merged here (notify script, binary verification, release-notes
# pruning).
#
# DELIBERATELY NOT merged from the deleted file — these were divergences, not
# omissions, and copying them would change the deployed runtime:
#   * `npm install -g @anthropic-ai/claude-code` — this image does NOT bundle
#     claude. The host's proven binary is mounted read-only from
#     /home/andros/.local and symlinked onto PATH below (v4.0.41), so the
#     deployed agent is byte-identical to the team's. Bundling a second copy
#     would reintroduce version drift.
#   * `useradd andros` + `USER andros` + docker group GID 110 — this container
#     runs as ROOT on purpose; the prod compose sets IS_SANDBOX=1 so claude
#     accepts --dangerously-skip-permissions inside the sandboxed container
#     (v4.0.42). Adding a non-root user here would break agent dispatch.
#   * NodeSource Node 20 — Debian's nodejs/npm is what the deployed image has
#     been running on; swapping the runtime is a change of its own, not a fix.
#
# If a backend build problem needs fixing, it gets fixed HERE. Do not add a
# second Dockerfile.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage: release-notes — isolate ONLY the per-version RELEASE_NOTES.md files
# (the "Aktualizácie" changelog, served by GET /api/v1/release-notes).
#
# A flat `COPY docs/specs/versions/*/RELEASE_NOTES.md ./…` would collapse every
# match into one dir by basename and collide, losing the v<X>/ parent the
# service reads as the version. Copying the tree here then pruning everything
# except RELEASE_NOTES.md preserves that structure, and — the point — keeps the
# REST of docs/specs (development-spec, customer-dialogue, F-xxx internal dev
# docs) in this throwaway stage only. The previous flat copy shipped all of it:
# the v4.0.76 PROD image carried 132 files under /app/docs, 56 of them internal
# design documents that no endpoint reads. `backend.services.release_notes`
# already documented the pruned contract; now the deployed image honours it.
#
# `-mindepth 1` keeps the `versions/` root itself even when NO RELEASE_NOTES.md
# exists yet, so the runtime `COPY --from` below never fails on a missing
# source (it just copies an empty dir → the endpoint returns []).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS release-notes

WORKDIR /notes
COPY docs/specs/versions/ ./docs/specs/versions/
RUN find docs/specs/versions -type f ! -name 'RELEASE_NOTES.md' -delete \
    && find docs/specs/versions -mindepth 1 -type d -empty -delete

# ---------------------------------------------------------------------------
# Stage: base — the runtime image (default build target; must stay last).
# ---------------------------------------------------------------------------
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

# Verify the CLIs the backend shells out to actually landed — a silent apt install
# failure would otherwise surface only at runtime, as a FileNotFoundError in the
# middle of a Create Project or a UAT deploy (the §9.1 NEX Inbox v0.1.0 lesson).
# `command -v` rather than a fixed path: docker-ce-cli may install into /usr/bin
# or /usr/local/bin depending on Debian packaging.
RUN docker_bin="$(command -v docker)" && test -x "$docker_bin" \
    && gh_bin="$(command -v gh)" && test -x "$gh_bin" \
    && git_bin="$(command -v git)" && test -x "$git_bin" \
    && docker compose version \
    && echo "Docker CLI at $docker_bin (+ compose plugin), GitHub CLI at $gh_bin, git at $git_bin"

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
# Create-Project scaffolding reads charter/CI/smoke templates from /app/templates (agent-shared-base.md,
# ai-agent-charter.md, auditor-charter.md, release_smoke_test.sh, github-actions-workflow.yml, uat/, …).
# Without this, v2 charter provisioning fails "shared base template missing" and create 500s (v4.0.39).
COPY templates/ ./templates/
# Bake the notify script so send_telegram works in ANY instance. backend.services.notify prefers this
# baked copy and otherwise falls back to /opt/projects/nex-studio/scripts/notify_telegram.sh — a path
# that belongs to a DIFFERENT project's checkout and only resolves while /opt/projects happens to be
# mounted and happens to contain it. The v4.0.76 PROD image had no /app/scripts at all, so every
# Telegram nudge rode on that coincidence. (The deleted backend/Dockerfile had this COPY; nothing built
# from it, which is exactly how the gap survived.)
COPY scripts/notify_telegram.sh ./scripts/notify_telegram.sh
# The Aktualizácie changelog is served from the per-version RELEASE_NOTES.md files
# (backend.services.release_notes reads /app/docs/specs/versions/v*/RELEASE_NOTES.md).
# Without this the endpoint returns [] and the Aktualizácie tab is empty (v4.0.33).
# Taken from the pruned stage above — ONLY the notes files, never the full docs/specs tree.
COPY --from=release-notes /notes/docs/specs/versions/ ./docs/specs/versions/

EXPOSE 9176

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -m backend.scripts.healthcheck

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9176"]
