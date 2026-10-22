"""DEV-176 — what a build leaves behind (``*.tsbuildinfo``, TypeScript's incremental cache) stays out of git.

The project template's ``.gitignore`` did not name it, so the files ended up in git: the switch of Career Asistent to
Web committed the web's two with the web, the agent its frontend's, NEX Websites carries two more (measured
21.10.2026). After every type check they show as changes nobody made. The template ignores them now; a project that
still tracks them loses them here, in one commit of the cockpit's, after a finished task
(``orchestrator._push_after_task``) — the files stay on disk, git stops following them. Director: „Súhlasím“,
„Poradie nechám na teba“.
"""

from __future__ import annotations

import fnmatch
import logging
import subprocess
from pathlib import Path, PurePosixPath
from typing import Optional

from backend.services import ci_runs

logger = logging.getLogger(__name__)

PATTERN = "*.tsbuildinfo"
COMMIT_MESSAGE = "chore: build leftovers (*.tsbuildinfo) stay out of git"
FOREIGN_CHANGES = "pozostatky stavby ostali v gite — v projekte sú cudzie zmeny, ktoré by sa primiešali do commitu"
_TIMEOUT = 60


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=_TIMEOUT, check=False
    )


def keep_out_of_git(project_root: Path) -> Optional[str]:
    """Name :data:`PATTERN` in the project's root ``.gitignore`` and stop tracking every file it matches — in one commit
    of these paths only, without the pre-commit check (it would type-check a frontend for a cache file). ``None`` when
    there was nothing to do or it is done; otherwise why not, in the manager's words. Never raises.

    Someone else's change — anything staged but a leftover, or an unsaved edit of ``.gitignore`` — is never swept into
    this commit: then nothing happens and the reason says so. A staged leftover is this cleanup's own: Career Asistent's
    two were left staged by a killed commit (DEV-180) and kept the cleanup from ever running. Nor is a red proof the
    agent stayed on (DEV-173): its newest commit must stay the proof, or the cockpit would no longer know it for one —
    the next task, back on the working branch, does the cleanup."""
    try:
        if not (project_root / ".git").exists():
            return None
        tracked = [path for path in _git(project_root, "ls-files", "-z", "--", PATTERN).stdout.split("\0") if path]
        gitignore = project_root / ".gitignore"
        lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
        if not tracked and PATTERN in lines:
            return None
        if _git(project_root, "log", "-1", "--format=%s").stdout.lower().startswith(ci_runs.PROOF_MARK):
            return None
        staged = [
            path for path in _git(project_root, "diff", "--cached", "--name-only", "-z").stdout.split("\0") if path
        ]
        foreign = [path for path in staged if not fnmatch.fnmatch(PurePosixPath(path).name, PATTERN)]
        if foreign or _git(project_root, "diff", "--name-only", "--", ".gitignore").stdout.strip():
            return FOREIGN_CHANGES
        if PATTERN not in lines:
            text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
            separator = "" if not text or text.endswith("\n") else "\n"
            gitignore.write_text(
                f"{text}{separator}# Build leftovers — TypeScript's incremental cache (DEV-176)\n{PATTERN}\n",
                encoding="utf-8",
            )
            _git(project_root, "add", "--", ".gitignore")
        if tracked:
            _git(project_root, "rm", "--cached", "-q", "--", *tracked)
        if _git(project_root, "diff", "--cached", "--quiet").returncode == 0:
            return None  # only leftovers that git had never committed — out of the index, nothing to commit
        done = _git(project_root, "commit", "--no-verify", "-q", "-m", COMMIT_MESSAGE)
        if done.returncode != 0:
            logger.warning("build leftovers commit failed in %s: %s", project_root, done.stderr.strip()[:300])
            return "pozostatky stavby sa z gitu nepodarilo odobrať — commit zlyhal"
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("build leftovers could not be kept out of git in %s: %s", project_root, exc)
        return "pozostatky stavby sa z gitu nepodarilo odobrať"
