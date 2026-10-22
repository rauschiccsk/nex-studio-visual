"""DEV-175 — a version is founded only over saved work, whichever way the Manažér founds it.

The check lived on the New-Version page alone (v4.0.25, ``DirtyTreeGuard``); the backend founded a version over any
working tree. On 21.10.2026 Career Asistent 1.1.0 was founded from Dedo's brief with 13 unsaved changes — that button
never asked. The agent then meets work nobody explains and asks the Manažér a question only Dedo could answer. Every
route that founds a version now asks :func:`refusal` first and answers 409 with what is unsaved, so each screen offers
the same Uložiť / Zahodiť as the New-Version page. The cockpit's own files (DEV-174) do not count. Director 21.10.2026:
„Súhlasím“, „Poradie nechám na teba“.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Optional
from uuid import UUID

from backend.services import git_state, workspace_owner

logger = logging.getLogger(__name__)

#: How long an async route lets the check run off the event loop — above ``git status``'s own 30 s.
CHECK_CAP = 45

UNSAVED = (
    "Projekt má neuložené zmeny ({count}). Ulož ich alebo zahoď — inak by kokpit nevedel, čo do novej verzie patrí. "
    "Verzia sa nezaložila."
)
COULD_NOT_CHECK = (
    "Kokpit nevedel overiť, či projekt nemá neuložené zmeny, a cez neoverené zmeny verziu nezakladá. Verzia sa "
    "nezaložila — skús to znova o chvíľu."
)

_CLEAN = {"clean": True, "dirty_count": 0, "files": [], "truncated": False}


def status(source_path: Optional[str]) -> dict:
    """The project's unsaved work — what the New-Version page shows (``GET /projects/{id}/git-status``) and what
    founding is refused over: one answer for both.

    A project with no source path, or whose workspace is not a git repository, has nothing to guard and is clean. The
    second used to be an error, which left founding blocked behind a guard that had nothing to show. Raises
    ``ValueError`` when git cannot tell."""
    if not source_path:
        return dict(_CLEAN)
    if not git_state.workspace_is_git_repo(source_path):
        return {**_CLEAN, "not_a_repo": True}
    # DEV-174: the cockpit's own files (agents' rules and skills, the task plan) are not the Manažér's unsaved work.
    return git_state.working_tree_status(source_path, exclude=workspace_owner.COCKPIT_WRITES)


def refusal(project_id: UUID, source_path: Optional[str]) -> Optional[dict]:
    """``None`` when a version may be founded; otherwise the body of the 409 — the sentence for the Manažér, the project
    whose work it is and what is unsaved (the shape of :func:`status`), so a screen that knows only a version can still
    save or discard it.

    When git cannot tell, founding is refused as well — „couldn't ask“ is not „clean“ — and the body carries no list."""
    try:
        found = status(source_path)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        logger.warning("unsaved work of project %s could not be checked: %s", project_id, exc)
        return {"message": COULD_NOT_CHECK, "project_id": str(project_id)}
    if found["clean"]:
        return None
    return {
        "message": UNSAVED.format(count=found["dirty_count"]),
        "project_id": str(project_id),
        "unsaved_work": found,
    }
