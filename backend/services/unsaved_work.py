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
from pathlib import Path
from typing import Optional
from uuid import UUID

from backend.services import cockpit_commit, git_state, workspace_owner

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

#: What the unsaved-work check leaves out: the cockpit's own files (DEV-174) and the agent's memory (DEV-451).
_NOT_THE_MANAGERS = (*workspace_owner.COCKPIT_WRITES, *workspace_owner.AGENT_MEMORY)
#: DEV-451: the commit that keeps what the agent noted outside its turns' commits.
MEMORY_COMMIT = "chore(memory): the agent's notes from its previous work"


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
    # DEV-451: nor is the agent's memory — it is saved when a version is founded (:func:`refusal`).
    return git_state.working_tree_status(source_path, exclude=_NOT_THE_MANAGERS)


def save(source_path: str, message: str) -> dict:
    """„Uložiť ich“ — commits exactly what :func:`status` shows (DEV-203): the cockpit's own files stay out of the
    project's git, as they stay off the card. ``git_state.commit_all``'s answer."""
    return git_state.commit_all(source_path, message, exclude=_NOT_THE_MANAGERS)


def discard(source_path: str) -> dict:
    """„Zahodiť“ — throws away exactly what :func:`status` shows (DEV-203) and leaves the cockpit's own files as the
    cockpit wrote them. ``git_state.discard_all``'s answer."""
    return git_state.discard_all(source_path, exclude=_NOT_THE_MANAGERS)


def refusal(project_id: UUID, source_path: Optional[str]) -> Optional[dict]:
    """``None`` when a version may be founded; otherwise the body of the 409 — the sentence for the Manažér, the project
    whose work it is and what is unsaved (the shape of :func:`status`), so a screen that knows only a version can still
    save or discard it.

    When git cannot tell, founding is refused as well — „couldn't ask“ is not „clean“ — and the body carries no list.

    DEV-451: the agent's memory is saved first — 20.11.2026 Orthodox Hermenia: after 0.2.0 its ``MEMORY.md`` stayed
    outside git and the next fast fix stopped on „Projekt má 1 zmena, ktoré neboli uložené“, a question about the
    agent's own notes the Manažér could only guess at."""
    save_agent_memory(source_path)
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


def save_agent_memory(source_path: Optional[str]) -> Optional[str]:
    """DEV-451 — commit what the agent noted in its memory and did not commit itself; ``None`` when it is in git (or
    there was nothing), else why not (logged — a memory that stays unsaved never blocks founding, it is left out of
    the unsaved work). Nothing else is committed."""
    if not source_path or not git_state.workspace_is_git_repo(source_path):
        return None
    # Notes in Markdown, no code: the project's check (type-checks, up to minutes) would only outlast the founding's
    # CHECK_CAP — as the template's pins, they are committed without it (DEV-400); the project's CI sees the commit.
    why = cockpit_commit.commit(Path(source_path), ["MEMORY.md", ".memory"], MEMORY_COMMIT, verify=False)
    if why:
        logger.warning("the agent's memory in %s was not saved: %s", source_path, why)
    return why
