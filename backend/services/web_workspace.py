"""DEV-161 — a project switched to Web gets its public web on disk: the same ``web/`` a founded Web project gets.

Switching to Web (DEV-160) changed only the cockpit's table. The project's workspace still had one frontend, so the
first build after the switch would have had no ``web/`` to work on, no CI checking it and a pre-commit check that knew
only ``frontend/``. :func:`add_web` writes all three from the same templates the founding uses and commits them as one
change — before the switch is kept, so a web that cannot be built leaves the project as it was.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.services import create_project_postscaffold as postscaffold
from backend.services import git_state, template_bootstrap
from backend.services.project_surfaces import WEB_DIRECTORY

logger = logging.getLogger(__name__)

COMMIT_MESSAGE = "feat(web): the public web (web/) — sign-in by e-mail, registration — with its CI and pre-commit check"


class WebWorkspaceError(RuntimeError):
    """The web could not be added — the message is the Slovak reason the Manažér reads."""


def add_web(db: Session, project: Project) -> list[str]:
    """Write the public web into *project*'s workspace — ``web/`` from the template, its CI when the project has CI,
    the pre-commit check for every frontend — and commit + push them as one change.

    Raises :class:`WebWorkspaceError` when ``web/`` cannot be built; nothing stays behind then. Returns the Slovak
    sentences of what finished only partly (a commit or a push that did not go through) — reported, never raised."""
    target = Path(project.source_path or "")
    # The switch commits its own files only — but work someone left unsaved in the workspace would ride along with
    # the next build unseen, so the switch waits until the workspace is clean.
    try:
        tree = git_state.working_tree_status(str(target))
    except ValueError as exc:
        raise WebWorkspaceError("Priečinok projektu nie je čitateľný git repozitár, web sa doňho nedá pridať.") from exc
    if not tree["clean"]:
        raise WebWorkspaceError(
            f"V priečinku projektu sú neuložené zmeny ({tree['dirty_count']}) — najprv ich ulož alebo zahoď, "
            "potom prepni projekt na Web."
        )
    web = target / WEB_DIRECTORY
    # A web/ already in git is the result of an earlier switch that did not finish — it is kept, not built again.
    if not _tracked(target, WEB_DIRECTORY):
        # Only a web/ this attempt creates may be removed after a failure — one that was there before is not ours.
        existed = web.exists()
        try:
            template_bootstrap.add_web_skeleton(db, project)
        except template_bootstrap.TemplateBootstrapError as exc:
            if project.source_path and not existed and web.is_dir():
                shutil.rmtree(web, ignore_errors=True)
                logger.info("removed the partial web/ of slug=%s after a failed add", project.slug)
            raise WebWorkspaceError(str(exc)) from exc

    candidates = [WEB_DIRECTORY]
    # The web's CI goes only where CI runs at all — a project founded without CI gets none here either.
    if (target / ".github" / "workflows" / "ci.yml").is_file():
        postscaffold.write_web_workflow(target, project.slug)
        candidates.append(str(postscaffold.WEB_WORKFLOW))
    # A project that has the pre-commit check gets the version that checks every frontend.
    if (target / ".githooks" / "pre-commit").is_file() and postscaffold.write_precommit_hook(target):
        candidates.append(".githooks/pre-commit")

    # Only what this switch really changed — a retry after an unfinished switch commits just what is still missing.
    paths = [path for path in candidates if _changed(target, path)]
    if not paths:
        return []
    note = postscaffold.commit_and_push(target, paths, COMMIT_MESSAGE, project.slug, scaffold=True)
    return [note] if note else []


def _git(target: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(target), *args],
        capture_output=True,
        text=True,
        timeout=postscaffold.CICD_TIMEOUT,
        check=False,
    )


def _tracked(target: Path, path: str) -> bool:
    """Is *path* in the project's last commit?"""
    return _git(target, "ls-files", "--error-unmatch", "--", path).returncode == 0


def _changed(target: Path, path: str) -> bool:
    """Does *path* differ from the last commit (new files included)?"""
    return bool(_git(target, "status", "--porcelain", "--", path).stdout.strip())
