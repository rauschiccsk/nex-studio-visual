"""Ownership of a project's workspace — the backend runs as root, the tooling that works in the workspace does not."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

# The standard owner for a project workspace: uid:gid 1000:1000 — the host user, matching every existing
# tree under ``/opt/projects`` AND the Vizuál sandbox's ``SANDBOX_USER``. It is NOT the backend's own uid:
# the backend container runs as root (0:0), so a freshly scaffolded workspace lands root-owned. That breaks
# any NON-root tooling that later operates on it — most concretely the Vizuál live-preview sandbox, which
# runs ``npm install`` as uid 1000 and cannot write ``node_modules`` into a root-owned ``frontend/`` (v4.0.44).
_WORKSPACE_OWNER = "1000:1000"

#: DEV-174 — what the cockpit itself writes into a project's workspace and never commits there: the agents' rules
#: and skills (refreshed before every new session, DEV-47 — HEAD must not move) and each version's task plan, written
#: from the database (``orchestrator._write_task_plan_doc``). As git pathspec globs. The agent's own permissions
#: forbid editing ``.claude/agents`` and ``.claude/skills``, so no work of the agent can be there; the check of
#: unsaved changes leaves these out, or the Manažér is asked to save or throw away files that were never his.
COCKPIT_WRITES: tuple[str, ...] = (
    ".claude/agents/**",
    ".claude/skills/**",
    "docs/specs/versions/*/spec/task-plan.md",
)


def chown_workspace(source_path: str | None) -> None:
    """Best-effort ``chown -R 1000:1000`` of a freshly created project workspace.

    The backend runs as root, so it creates the workspace root-owned; align it with ``/opt/projects``
    convention + the Vizuál sandbox uid so non-root tooling can operate on it. Logged, NEVER raises — a
    chown failure must not abort (or roll back) an otherwise successful create. root + the agent keep
    working afterwards (root bypasses ownership)."""
    if not source_path:
        return
    try:
        result = subprocess.run(
            ["chown", "-R", _WORKSPACE_OWNER, source_path],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "workspace chown to %s failed for %s: %s",
                _WORKSPACE_OWNER,
                source_path,
                (result.stderr or "").strip(),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("workspace chown to %s errored for %s: %s", _WORKSPACE_OWNER, source_path, exc)
