"""DEV-180 — what the cockpit commits into a project lands in git, or the Manažér hears why not.

On 21.10.2026 the approval of Career Asistent 1.1.0's Vizuál committed the screens and the documents in one commit and
gave it 15 s (``orchestrator._git_ok``). The project's pre-commit check — type-check and eslint of both frontends — took
18 s; the commit was killed and nobody was told. The Špecifikácia, the Návrh and the database structure of 1.1.0 stayed
on disk only, and from the first task on CI failed on main at the agent's test that reads the database structure — 30
tasks long. The Návrh's own commit had not landed either. A commit here takes only its own paths (what else waits in the
index stays out of it, and so does that check), has the time the project's check needs, runs off the main loop at the
caller and says what did not land. Director 22.10.2026: „Súhlasím“.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 60

#: How long an async caller lets :func:`commit` run off the event loop — the commit with the project's check, and the
#: four short git calls around it.
CAP = settings.project_commit_timeout_seconds + 4 * _GIT_TIMEOUT_SECONDS

NOT_STAGED = "git súbory neprijal na zápis"
REFUSED = "kontrola projektu pred zápisom neprešla ({reason})"
TOO_LONG = "zápis s kontrolou projektu neskončil do {seconds} s"
LEFT_OVER = "po zápise ostali nezapísané súbory ({count})"
NOT_RUN = "git sa nepodarilo spustiť"


def _git(root: Path, *args: str, timeout: int = _GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout, check=False)


def _known(root: Path, paths: Sequence[str]) -> list[str]:
    """The paths git can take: on disk, or tracked and gone (a moved file). ``git add`` refuses the whole call over one
    pathspec that matches nothing."""
    gone = [path for path in paths if not (root / path).exists()]
    tracked = [name for name in _git(root, "ls-files", "-z", "--", *gone).stdout.split("\0") if name] if gone else []
    return [
        path
        for path in paths
        if path not in gone or any(name == path or name.startswith(path.rstrip("/") + "/") for name in tracked)
    ]


def _commit(root: Path, known: Sequence[str], message: str) -> subprocess.CompletedProcess:
    """``git commit`` of ``known`` alone — the paths after ``--`` make it ``--only``, and the project's check then sees
    only them. It runs in its own process group: when it does not end in time, the whole group goes (git, the check and
    the ``tsc`` the check started) and so does the index lock git held for it — a killed commit left
    ``.git/index.lock`` behind, and every later git call in the project refused to run. Raises ``TimeoutExpired``."""
    proc = subprocess.Popen(
        ["git", "-C", str(root), "commit", "-q", "-m", message, "--", *known],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=settings.project_commit_timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        try:
            proc.communicate(timeout=_GIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("the killed commit in %s still holds its output open", root)
        # While this commit held the lock no other git could take it, so the lock left now is this commit's.
        if (root / ".git").is_dir():
            (root / ".git" / "index.lock").unlink(missing_ok=True)
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _reason(done: subprocess.CompletedProcess) -> str:
    """The line of the check's output that says what failed — its ``❌`` line, else its last line."""
    lines = [line.strip() for line in f"{done.stdout}\n{done.stderr}".splitlines() if line.strip()]
    failed = [line for line in lines if "❌" in line]
    return ((failed or lines or [f"kód {done.returncode}"])[-1])[:200]


def commit(project_root: Path, paths: Sequence[str], message: str) -> Optional[str]:
    """Commit the project's ``paths`` (relative to its root) and nothing else, through the project's pre-commit check.

    ``None`` when they are in git — committed now, nothing to commit, or no git at all; otherwise why not, in the
    Manažér's words (the details go to the log). Never raises."""
    root = Path(project_root)
    if not (root / ".git").exists():
        return None
    try:
        known = _known(root, paths)
        if not known:
            return None
        if _git(root, "add", "-A", "--", *known).returncode != 0:
            return NOT_STAGED
        if _git(root, "diff", "--cached", "--quiet", "--", *known).returncode == 0:
            return None
        try:
            done = _commit(root, known, message)
        except subprocess.TimeoutExpired:
            logger.warning(
                "commit of %s in %s did not end in %s s", known, root, settings.project_commit_timeout_seconds
            )
            return TOO_LONG.format(seconds=settings.project_commit_timeout_seconds)
        if done.returncode != 0:
            logger.warning(
                "commit of %s in %s failed: %s", known, root, f"{done.stdout}\n{done.stderr}".strip()[-2000:]
            )
            return REFUSED.format(reason=_reason(done))
        left = [line for line in _git(root, "status", "--porcelain", "--", *known).stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("commit of %s in %s could not run: %s", paths, root, exc)
        return NOT_RUN
    if left:
        logger.warning("after the commit in %s still not in git: %s", root, left[:20])
        return LEFT_OVER.format(count=len(left))
    return None
