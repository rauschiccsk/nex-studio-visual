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
from typing import Optional

from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.services import create_project_postscaffold as postscaffold
from backend.services import git_state, template_bootstrap
from backend.services.project_surfaces import WEB_DIRECTORY

logger = logging.getLogger(__name__)

COMMIT_MESSAGE = "feat(web): the public web (web/) — sign-in by e-mail, registration — with its CI and pre-commit check"
CLAUDE_MD = "CLAUDE.md"
NOT_A_REPOSITORY = "Priečinok projektu nie je čitateľný git repozitár, web sa doňho nedá pridať."


class WebWorkspaceError(RuntimeError):
    """The web could not be added — the message is the Slovak reason the Manažér reads."""


def add_web(db: Session, project: Project) -> list[str]:
    """Write the public web into *project*'s workspace — ``web/`` from the template, its CI when the project has CI,
    the pre-commit check for every frontend, the table of frontends in ``CLAUDE.md`` — and commit + push them as one
    change. The agents' charters get the Web addendum at their next session, from the switched type (DEV-165).

    Raises :class:`WebWorkspaceError` when what it writes has unsaved changes (:func:`unsaved_work`) or ``web/``
    cannot be built; nothing stays behind then. Returns the Slovak
    sentences of what finished only partly (a commit or a push that did not go through) — reported, never raised."""
    target = Path(project.source_path or "")
    refusal = unsaved_work(project)
    if refusal:
        raise WebWorkspaceError(refusal)
    paths = switch_paths(target)
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

    # DEV-165: the project's root CLAUDE.md names both frontends — the same table a founded Web project gets.
    claude_md = target / CLAUDE_MD
    section = postscaffold.web_claude_md_section()
    text = claude_md.read_text(encoding="utf-8")
    if section.strip() not in text:
        claude_md.write_text(text.rstrip("\n") + "\n" + section, encoding="utf-8")
    if str(postscaffold.WEB_WORKFLOW) in paths:
        postscaffold.write_web_workflow(target, project.slug)
    if str(postscaffold.PRECOMMIT_HOOK) in paths:
        postscaffold.write_precommit_hook(target)

    # Only what this switch really changed — a retry after an unfinished switch commits just what is still missing.
    changed = [path for path in paths if _changed(target, path)]
    if not changed:
        return []
    note = postscaffold.commit_and_push(target, changed, COMMIT_MESSAGE, project.slug, scaffold=True)
    return [note] if note else []


def switch_paths(target: Path) -> list[str]:
    """What the switch writes into the workspace *target* — and so all it commits: the web, the table of frontends in
    ``CLAUDE.md``, the web's CI where the project has CI at all, the check for every frontend where the project has
    the pre-commit check."""
    paths = [WEB_DIRECTORY, CLAUDE_MD]
    if (target / ".github" / "workflows" / "ci.yml").is_file():
        paths.append(str(postscaffold.WEB_WORKFLOW))
    if (target / postscaffold.PRECOMMIT_HOOK).is_file():
        paths.append(str(postscaffold.PRECOMMIT_HOOK))
    return paths


def unsaved_work(project: Project) -> Optional[str]:
    """Why the switch has to wait for the workspace — the Slovak sentence the Manažér reads — or ``None``.

    The switch commits exactly :func:`switch_paths`, so an unsaved change there would ride along in its commit and be
    pushed unseen; it waits for those. Changes anywhere else stay exactly as they were — the agents' rules the cockpit
    refreshes on purpose without a commit (DEV-47) and build leftovers made every project look unsaved, and Career
    Asistent's switch was refused although the panel said it could be done (Director 21.10.2026: „Áno oprav.“). The
    preview asks this too, so the panel never offers a switch that is then refused."""
    target = Path(project.source_path or "")
    try:
        # Raises for a workspace that is missing or no git repository — the switch could not commit there either.
        tree = git_state.working_tree_status(project.source_path or "", paths=switch_paths(target))
    except ValueError:
        return NOT_A_REPOSITORY
    if tree["clean"]:
        return None
    shown = ", ".join(entry["path"] for entry in tree["files"][:5]) + (" …" if tree["dirty_count"] > 5 else "")
    return (
        f"V tom, čo prepnutie zapisuje, sú neuložené zmeny ({shown}) — ulož ich alebo zahoď na stránke novej verzie "
        "projektu, potom prepni projekt na Web."
    )


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
