"""Subprocess invocation of ``icc-claude-template/init.sh`` from the
project creation flow.

When a new project is created via ``POST /api/v1/projects``, NEX Studio
delegates the filesystem-side bootstrap (mkdir, git init, copy of
CLAUDE.md / skills / hooks / scripts, first commit) to the
``icc-claude-template`` script — single source of truth, see D-021
revocation entry in DECISIONS.md and the Phase 3 plan.

This module provides the thin Python wrapper:

* :func:`invoke_init_script` — build the argument list from the project
  record and run the subprocess. Returns on success, raises
  :class:`TemplateBootstrapError` with structured detail on failure.

The init.sh path and timeout are sourced from ``system_settings``
(``template_init_script_path``, ``template_init_timeout_seconds``) so
operators can swap the template (forks, frozen versions) without a
code change.

Per CLAUDE.md §13 — this module never reads `.env`, never logs the
GitHub token or any other credential. Init.sh receives only the public
project metadata (name, slug, ports, repo URL, target path).
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.services import system_setting as system_setting_service

logger = logging.getLogger(__name__)


class TemplateBootstrapError(RuntimeError):
    """Raised when init.sh subprocess fails or pre-conditions are unmet."""


@dataclass(frozen=True)
class BootstrapResult:
    """Captures the outcome of a successful init.sh run.

    Used by callers to log + include in the API response body so the
    Director can verify which template ran and when. ``stdout`` is
    the trimmed last 50 lines of script output (full output goes to
    backend logs).
    """

    target: str
    init_script: str
    stdout_tail: str


# init.sh expects --port-base to be a multiple of 10 and the project's
# backend_port lives in the same 10-port block (per ICC Port Registry
# v2 / D-020). NEX Studio's port allocation is column-by-column with
# no enforced contiguous-block check, so we derive the base from
# ``backend_port`` rounded down to nearest 10. If the actual frontend
# / db / ui_design ports diverge from base+1 / +2 / +3 the project
# still works — init.sh's port block in CLAUDE.md is reference-only,
# not enforced at runtime.
def _port_base_from_backend(backend_port: int | None) -> int | None:
    if backend_port is None:
        return None
    return backend_port - (backend_port % 10)


# Extract owner/name from a GitHub URL. ``init.sh --repo`` requires
# the ``owner/name`` short form (regex ``^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$``).
# Returns None if the URL doesn't match, in which case we fall back to
# a sensible default (``rauschiccsk/<slug>``) — init.sh stores it as
# metadata only, so an approximation is acceptable for bootstrap.
_GH_URL_PATTERN = re.compile(
    r"https?://github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+?)(?:\.git)?/?$"
)


def _repo_from_url(repo_url: str | None, slug: str) -> str:
    if repo_url:
        match = _GH_URL_PATTERN.match(repo_url.strip())
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    # Fallback — init.sh requires --repo, default to rauschiccsk org.
    return f"rauschiccsk/{slug}"


def invoke_init_script(db: Session, project: Project) -> BootstrapResult:
    """Run the icc-claude-template init.sh for the given project.

    Pre-conditions:
        * ``template_init_script_path`` system setting is non-empty
          and points at an executable file.
        * The script's parent directory and the project ``source_path``
          parent are reachable from the process (bind-mount in
          ``docker-compose.yml`` for backend service).

    Args:
        db: Active session — used to resolve typed system_settings.
        project: The just-created Project row (still uncommitted in
            the caller's transaction). The function reads
            ``name`` / ``slug`` / ``description`` / ``backend_port``
            / ``repo_url`` / ``source_path``.

    Returns:
        BootstrapResult with the target path, init.sh path, and
        trimmed stdout for logging / response body.

    Raises:
        TemplateBootstrapError: If the script is not configured, not
            found, or the subprocess exits non-zero or times out.
    """
    init_script_path = system_setting_service.get_str(db, "template_init_script_path")
    if not init_script_path:
        raise TemplateBootstrapError(
            "Auto-bootstrap disabled — template_init_script_path is empty in "
            "system_settings. Either set the path or invoke init.sh manually "
            "after project creation."
        )

    script = Path(init_script_path)
    if not script.is_file():
        raise TemplateBootstrapError(
            f"template_init_script_path points at a non-existent file: {init_script_path}"
        )

    if not project.source_path:
        raise TemplateBootstrapError(
            "Project.source_path is empty — cannot bootstrap. The path "
            "template should auto-fill it from the slug at creation time "
            "(see project_service.create + default_source_path_template)."
        )

    port_base = _port_base_from_backend(project.backend_port)
    if port_base is None:
        raise TemplateBootstrapError(
            "Project.backend_port is empty — init.sh requires --port-base. "
            "Allocate a port block before creating the project."
        )

    repo = _repo_from_url(project.repo_url, project.slug)

    args = [
        str(script),
        "--name", project.name,
        "--slug", project.slug,
        "--description", project.description or "",
        "--port-base", str(port_base),
        "--repo", repo,
        "--variant", "general",  # B1 decision (2026-05-03) — UI doesn't expose variant yet
        "--target", project.source_path,
        "--init-target",  # create the target dir if it doesn't exist
    ]

    timeout = system_setting_service.get_int(db, "template_init_timeout_seconds")

    logger.info(
        "Invoking template init.sh for project %s (slug=%s, target=%s, "
        "port-base=%d, repo=%s, timeout=%ds)",
        project.id,
        project.slug,
        project.source_path,
        port_base,
        repo,
        timeout,
    )

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,  # we inspect returncode manually for richer error
        )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "Template init.sh timed out after %ds for slug=%s",
            timeout,
            project.slug,
        )
        raise TemplateBootstrapError(
            f"Template bootstrap timed out after {timeout}s — partial state "
            f"may exist at {project.source_path}, manual cleanup required."
        ) from exc
    except OSError as exc:
        # Permission, file-system, executable-bit, etc.
        logger.error(
            "Template init.sh OSError for slug=%s: %s", project.slug, exc
        )
        raise TemplateBootstrapError(
            f"Template bootstrap OS error: {exc}"
        ) from exc

    if result.returncode != 0:
        # Log full stderr for triage; expose trimmed message to caller.
        logger.error(
            "Template init.sh exit=%d for slug=%s\nstdout:\n%s\nstderr:\n%s",
            result.returncode,
            project.slug,
            result.stdout,
            result.stderr,
        )
        # Trim stderr to the most relevant tail (init.sh errors print at end).
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-10:])
        raise TemplateBootstrapError(
            f"Template bootstrap failed (exit {result.returncode}): "
            f"{stderr_tail or '<no stderr>'}"
        )

    stdout_tail = "\n".join(result.stdout.strip().splitlines()[-50:])
    logger.info(
        "Template init.sh succeeded for slug=%s (target=%s)",
        project.slug,
        project.source_path,
    )

    return BootstrapResult(
        target=project.source_path,
        init_script=str(script),
        stdout_tail=stdout_tail,
    )
