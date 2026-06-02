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

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import system_setting as system_setting_service

logger = logging.getLogger(__name__)


class TemplateBootstrapError(RuntimeError):
    """Raised when init.sh subprocess fails or pre-conditions are unmet."""


class GitPushVerificationError(TemplateBootstrapError):
    """Raised by K-001 verify_push() when local HEAD != remote HEAD or remote not registered.

    Triggers K-002 rollback path in the caller.
    """


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
_GH_URL_PATTERN = re.compile(r"https?://github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+?)(?:\.git)?/?$")


def _repo_from_url(repo_url: str | None, slug: str) -> str:
    if repo_url:
        match = _GH_URL_PATTERN.match(repo_url.strip())
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    # Fallback — init.sh requires --repo, default to rauschiccsk org.
    return f"rauschiccsk/{slug}"


def invoke_init_script(
    db: Session,
    project: Project,
    *,
    dry_run: bool = False,
    enable_coordinator: bool = True,
) -> BootstrapResult:
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
        dry_run: When True, ``--dry-run`` is appended to the init.sh
            invocation. The script then validates arguments and logs
            planned actions without making any filesystem / git side
            effects. Used by integration tests to exercise the API +
            schema + service layer without polluting ``/opt/projects/``
            and ``/home/icc/knowledge/projects/``. Production callers
            never set this.

    Returns:
        BootstrapResult with the target path, init.sh path, and
        trimmed stdout for logging / response body.

    Raises:
        TemplateBootstrapError: If the script is not configured, not
            found, or the subprocess exits non-zero or times out.
    """
    init_script_path = system_setting_service.get_str(db, "template_init_script_path")
    if not init_script_path:
        # Empty path = operator opt-out (per docstring). Graceful no-op
        # so test environments + brownfield projects can disable
        # auto-bootstrap without 500-ing project creation.
        logger.info(
            "Auto-bootstrap disabled (template_init_script_path empty) — skipping subprocess for slug=%s",
            project.slug,
        )
        return BootstrapResult(target=project.source_path or "", init_script="", stdout_tail="")

    script = Path(init_script_path)
    if not script.is_file():
        raise TemplateBootstrapError(f"template_init_script_path points at a non-existent file: {init_script_path}")

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
        "--name",
        project.name,
        "--slug",
        project.slug,
        "--description",
        project.description or "",
        "--port-base",
        str(port_base),
        "--repo",
        repo,
        "--variant",
        "general",  # B1 decision (2026-05-03) — UI doesn't expose variant yet
        "--target",
        project.source_path,
        "--init-target",  # create the target dir if it doesn't exist
    ]
    if dry_run:
        args.append("--dry-run")
    if not enable_coordinator:
        # F-004 K-003 opt-out (default in init.sh is enabled)
        args.append("--no-coordinator")

    # CR-NS-012: route agent notifications to the project owner. init.sh
    # writes the value into the new project's .env as TELEGRAM_NOTIFY_CHAT_ID.
    # Omitted when there is no owner or the owner has no chat_id configured
    # (→ no notifications, never blocks creation).
    if project.owner_id is not None:
        owner = db.get(User, project.owner_id)
        chat_id = (owner.telegram_chat_id or "").strip() if owner else ""
        if chat_id:
            args.extend(["--notify-chat-id", chat_id])

    timeout = system_setting_service.get_int(db, "template_init_timeout_seconds")

    logger.info(
        "Invoking template init.sh for project %s (slug=%s, target=%s, port-base=%d, repo=%s, timeout=%ds, dry_run=%s)",
        project.id,
        project.slug,
        project.source_path,
        port_base,
        repo,
        timeout,
        dry_run,
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
        logger.error("Template init.sh OSError for slug=%s: %s", project.slug, exc)
        raise TemplateBootstrapError(f"Template bootstrap OS error: {exc}") from exc

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
            f"Template bootstrap failed (exit {result.returncode}): {stderr_tail or '<no stderr>'}"
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


# ─────────────────────────────────────────────────────────────────────────────
# F-004 Stage 4 — Remote setup + K-001 verify + K-002 rollback (per spec §3.1/§3.2)
# ─────────────────────────────────────────────────────────────────────────────


def _run_git(args: list[str], *, cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a git command in ``cwd``. Captures stdout+stderr; returns CompletedProcess."""
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _run_gh(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a ``gh`` CLI command. Captures stdout+stderr; returns CompletedProcess."""
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def push_and_verify(
    *,
    target: str,
    repo_full_name: str,
    remote_url: str | None = None,
    push_retry_attempts: int = 1,
    timeout: int = 60,
) -> None:
    """Configure origin + push initial commit + verify (K-001).

    Args:
        target: Local project directory (must already be a git repo after init.sh).
        repo_full_name: ``owner/name`` form (e.g. ``rauschiccsk/nex-foo``).
        remote_url: Full git URL. Defaults to ``https://github.com/<repo_full_name>.git``
            (HTTPS, authenticated via the gh credential helper — the backend
            container has no ``ssh`` binary).
        push_retry_attempts: How many transient-failure retries to attempt
            (default 1 = one retry on top of initial attempt per spec §3.2).
        timeout: Per-subprocess timeout in seconds.

    Raises:
        GitPushVerificationError: If remote add, push, or HEAD-equality verify
            fails after all retries. Caller invokes :func:`rollback_partial_state`.
        TemplateBootstrapError: For non-recoverable conditions (target missing,
            git not initialized).
    """
    target_path = Path(target)
    if not (target_path / ".git").is_dir():
        raise TemplateBootstrapError(f"push_and_verify: {target} is not a git repository (init.sh did not run?)")

    final_url = remote_url or f"https://github.com/{repo_full_name}.git"

    # Step 1: Add origin remote (idempotent — replace if already exists)
    existing = _run_git(["remote", "get-url", "origin"], cwd=target, timeout=timeout)
    if existing.returncode == 0:
        # Remote already exists — update URL to be safe
        result = _run_git(["remote", "set-url", "origin", final_url], cwd=target, timeout=timeout)
    else:
        result = _run_git(["remote", "add", "origin", final_url], cwd=target, timeout=timeout)
    if result.returncode != 0:
        raise GitPushVerificationError(
            f"git remote add/set-url failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    # Step 1b: wire the HTTPS credential helper so `git push` authenticates via
    # the gh token. The backend container has no ``ssh`` binary, so the default
    # origin URL is HTTPS (see ``final_url``); ``gh auth setup-git`` is
    # idempotent and sets ``credential.https://github.com.helper``. A non-zero
    # exit is non-fatal — the push below surfaces the real error if credentials
    # are genuinely missing.
    gh_setup = _run_gh(["auth", "setup-git"], timeout=timeout)
    if gh_setup.returncode != 0:
        logger.warning(
            "gh auth setup-git returned %d (continuing; push will surface any credential error): %s",
            gh_setup.returncode,
            gh_setup.stderr.strip(),
        )

    # Step 2: Push (with retry on transient failure)
    push_attempts = 0
    max_attempts = push_retry_attempts + 1
    last_err = ""
    while push_attempts < max_attempts:
        push_attempts += 1
        result = _run_git(["push", "-u", "origin", "main"], cwd=target, timeout=timeout)
        if result.returncode == 0:
            logger.info(
                "git push -u origin main succeeded on attempt %d/%d (repo=%s)",
                push_attempts,
                max_attempts,
                repo_full_name,
            )
            break
        last_err = result.stderr.strip()
        logger.warning(
            "git push attempt %d/%d failed (repo=%s): %s",
            push_attempts,
            max_attempts,
            repo_full_name,
            last_err,
        )
    else:
        raise GitPushVerificationError(f"git push failed after {max_attempts} attempts: {last_err}")

    # Step 3: K-001 verification — local HEAD == remote HEAD
    local = _run_git(["rev-parse", "HEAD"], cwd=target, timeout=timeout)
    if local.returncode != 0:
        raise GitPushVerificationError(f"K-001 verify: cannot read local HEAD ({local.stderr.strip()})")
    local_head = local.stdout.strip()

    remote_ls = _run_git(["ls-remote", "origin", "HEAD"], cwd=target, timeout=timeout)
    if remote_ls.returncode != 0:
        raise GitPushVerificationError(f"K-001 verify: ls-remote origin failed ({remote_ls.stderr.strip()})")
    # ls-remote output: "<sha>\tHEAD"
    remote_head_line = remote_ls.stdout.strip().splitlines()[0] if remote_ls.stdout.strip() else ""
    remote_head = remote_head_line.split("\t")[0] if remote_head_line else ""

    if not remote_head:
        raise GitPushVerificationError("K-001 verify: ls-remote returned empty (remote HEAD missing)")
    if local_head != remote_head:
        raise GitPushVerificationError(
            f"K-001 verify: local HEAD ({local_head[:12]}) != remote HEAD ({remote_head[:12]})"
        )

    logger.info(
        "K-001 push verification PASSED (repo=%s, HEAD=%s)",
        repo_full_name,
        local_head[:12],
    )


def rollback_partial_state(
    *,
    target: str,
    repo_full_name: str,
    delete_github_repo: bool = False,
    timeout: int = 30,
) -> None:
    """K-002: cleanup partial scaffold after push failure.

    Removes the local ``.git`` directory (project files stay so the next
    create-project re-run can resume idempotently). Optionally deletes the
    GitHub repo if ``delete_github_repo=True`` — caller responsibility to
    confirm with Director before passing this flag.

    Args:
        target: Local project directory.
        repo_full_name: ``owner/name`` of the GitHub repo.
        delete_github_repo: If True, runs ``gh repo delete --yes``. Must be
            explicitly opted-in (Director confirmation) — never default.
        timeout: Per-subprocess timeout.

    Raises:
        TemplateBootstrapError: If cleanup itself fails (rare — logged for
            manual investigation).
    """
    target_path = Path(target)
    git_dir = target_path / ".git"

    if git_dir.is_dir():
        logger.warning(
            "K-002 rollback: removing local .git at %s (idempotent re-run safe)",
            git_dir,
        )
        # Use subprocess rm -rf — Path.rmtree would need walk + handle errors
        result = subprocess.run(
            ["rm", "-rf", str(git_dir)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise TemplateBootstrapError(
                f"K-002 rollback: rm -rf {git_dir} failed (exit {result.returncode}): {result.stderr.strip()}"
            )

    if delete_github_repo:
        logger.warning(
            "K-002 rollback: deleting GitHub repo %s (Director confirmation required)",
            repo_full_name,
        )
        result = subprocess.run(
            ["gh", "repo", "delete", repo_full_name, "--yes"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            # Non-fatal — repo may already be deleted, or gh auth missing.
            # Log + continue; admin can do manual cleanup if needed.
            logger.warning(
                "K-002 rollback: gh repo delete %s exit=%d (stderr=%s) — manual cleanup may be needed",
                repo_full_name,
                result.returncode,
                result.stderr.strip(),
            )

    logger.info("K-002 rollback complete (target=%s, repo=%s)", target, repo_full_name)
