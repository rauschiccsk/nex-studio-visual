"""DEV-367 — a version goes back from Návrh or Vizuál to Príprava: what happens in the project's git.

NEX Finance 0.1.0 reached Vizuál and the whole design was wrong. Director 12.11.2026: „Súhlasím, vrátime sa do prípravy,
pretože celý projekt je zlý.“ The Návrh, the task plan and the Vizuál are thrown away, but nothing is lost: the unsaved
work of the phase (the screens drawn in the Vizuál are never committed before its approval) lands in a commit of its own
first, and the next commit reverts it and removes the documents of the Návrh and the Vizuál. Both stay in git's history.

Local commits only, never pushed. Without the project's pre-commit check, like the cockpit's own housekeeping
(:mod:`backend.services.build_leftovers`): the first commit keeps work that is being discarded — half-drawn screens
need not pass the check, and a refusal would keep the discarded work in the tree — and the second one returns the tree
to what was committed before, minus the documents.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 60
#: How long the action lets the whole git part run off the event loop.
CAP = 120

TRACE_MESSAGE = "chore(vizual): rozpracovaný Vizuál verzie {version} pred vrátením do Prípravy"
REMOVAL_MESSAGE = "docs: Návrh a Vizuál verzie {version} vrátené do Prípravy — ostávajú v histórii gitu"

NOT_KEPT = "rozpracovaná práca sa nepodarila uložiť do gitu, takže ostala v projekte"
NOT_REVERTED = "rozpracovaná práca je uložená v gite, ale nepodarilo sa ju z projektu vrátiť"
NOT_REMOVED = "dokumenty Návrhu a Vizuálu sa nepodarilo z projektu odstrániť"
NOT_RUN = "git sa nepodarilo spustiť"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS, check=False
    )


def _failed(root: Path, step: str, done: subprocess.CompletedProcess) -> bool:
    if done.returncode == 0:
        return False
    logger.warning(
        "back to Príprava: git %s failed in %s: %s", step, root, f"{done.stdout}\n{done.stderr}".strip()[-500:]
    )
    return True


def discard(project_root: Path, version_number: str, paths: Sequence[str]) -> Optional[str]:
    """Keep the unsaved work in one commit, revert it and remove the tracked ``paths`` in the next one.

    ``None`` when it is done, when there was nothing to do, or when the project has no git; otherwise why not, in the
    Manažér's words (the details go to the log). Never raises. A clean tree gets only the second commit; a clean tree
    with nothing of ``paths`` tracked gets none."""
    root = Path(project_root)
    if not (root / ".git").exists():
        return None
    try:
        if _git(root, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            if _failed(root, "add", _git(root, "add", "-A")) or _failed(
                root,
                "commit",
                _git(root, "commit", "--no-verify", "-q", "-m", TRACE_MESSAGE.format(version=version_number)),
            ):
                return NOT_KEPT
            if _failed(root, "revert", _git(root, "revert", "--no-commit", "HEAD")):
                return NOT_REVERTED
        tracked = [name for name in _git(root, "ls-files", "-z", "--", *paths).stdout.split("\0") if name]
        if tracked and _failed(root, "rm", _git(root, "rm", "-q", "--", *tracked)):
            return NOT_REMOVED
        if _git(root, "diff", "--cached", "--quiet").returncode == 0:
            return None
        if _failed(
            root,
            "commit",
            _git(root, "commit", "--no-verify", "-q", "-m", REMOVAL_MESSAGE.format(version=version_number)),
        ):
            return NOT_REMOVED
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("back to Príprava: git could not run in %s: %s", root, exc)
        return NOT_RUN
    return None
