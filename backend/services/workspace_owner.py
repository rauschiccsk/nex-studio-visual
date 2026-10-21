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
