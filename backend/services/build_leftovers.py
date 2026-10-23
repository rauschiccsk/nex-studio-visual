"""What a build and the cockpit leave in a project but is not its code stays out of git.

* DEV-176 — the build's leftovers (``*.tsbuildinfo``, TypeScript's incremental cache). The project template's
  ``.gitignore`` did not name it, so the files ended up in git: the switch of Career Asistent to Web committed the
  web's two with the web, the agent its frontend's, NEX Websites carries two more (measured 21.10.2026). After every
  type check they show as changes nobody made. Director: „Súhlasím“, „Poradie nechám na teba“.
* DEV-196 — the agent's rules and skills, which the cockpit writes into ``.claude`` before every agent session
  (DEV-47). Four projects tracked 7–17 of them with 9–12 unsaved changes (measured 23.10.2026), and the agent's
  instructions would go to the customer with the code. A skill of the project's own (one without the cockpit's mark)
  stays tracked. Director 23.10.2026: „Áno, schvaľujem všetkých 14 bodov“.

The template ignores both now; a project that still tracks them loses them here, in one commit of the cockpit's, after
a finished task (``orchestrator._push_after_task``) — the files stay on disk, git stops following them.
"""

from __future__ import annotations

import fnmatch
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from backend.services import ci_runs, create_project_postscaffold

logger = logging.getLogger(__name__)

PATTERN = "*.tsbuildinfo"
FOREIGN_CHANGES = "pozostatky stavby ostali v gite — v projekte sú cudzie zmeny, ktoré by sa primiešali do commitu"
_TIMEOUT = 60


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=_TIMEOUT, check=False
    )


def _tracked(root: Path, *pathspecs: str) -> list[str]:
    return [path for path in _git(root, "ls-files", "-z", "--", *pathspecs).stdout.split("\0") if path]


@dataclass(frozen=True)
class Leftover:
    """One kind of what stays out of git: the lines of the root ``.gitignore`` that name it, which tracked paths are
    it, and how the cockpit's commit calls it."""

    what: str
    comment: str
    ignore: tuple[str, ...]
    owns: Callable[[Path, str], bool]
    candidates: tuple[str, ...]

    @property
    def commit_message(self) -> str:
        return f"chore: {self.what} stay out of git"

    def tracked(self, root: Path) -> list[str]:
        return [path for path in _tracked(root, *self.candidates) if self.owns(root, path)]


def _is_typescript_cache(_root: Path, path: str) -> bool:
    return fnmatch.fnmatch(PurePosixPath(path).name, PATTERN)


_AGENTS = ".claude/agents/"
_SKILLS = ".claude/skills/"
_ACTION_LOG = ".claude/wip-actions.log"


def _is_the_cockpits_rule(root: Path, path: str) -> bool:
    """A role's charter or settings, the action log of the cockpit's hook, or a skill the cockpit wrote — the old
    template's loose copies and every skill whose ``SKILL.md`` carries the cockpit's mark."""
    if path.startswith(_AGENTS) or path == _ACTION_LOG:
        return True
    if not path.startswith(_SKILLS):
        return False
    rest = PurePosixPath(path).relative_to(_SKILLS)
    if len(rest.parts) == 1:
        return rest.name in create_project_postscaffold._LOOSE_TEMPLATE_SKILLS
    skill = root / _SKILLS / rest.parts[0] / create_project_postscaffold.SKILL_FILE
    try:
        return create_project_postscaffold.COCKPIT_SKILL_MARKER in skill.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


TYPESCRIPT_CACHE = Leftover(
    what=f"build leftovers ({PATTERN})",
    comment="# Build leftovers — TypeScript's incremental cache (DEV-176)",
    ignore=(PATTERN,),
    owns=_is_typescript_cache,
    candidates=(PATTERN,),
)
AGENT_RULES = Leftover(
    what="the agent's rules (.claude)",
    comment="# The agent's rules and skills — the NEX Studio cockpit writes them before every session (DEV-196)",
    ignore=(f"/{_AGENTS}", f"/{_SKILLS}", f"/{_ACTION_LOG}"),
    owns=_is_the_cockpits_rule,
    candidates=(_AGENTS, _SKILLS, _ACTION_LOG),
)
LEFTOVERS: tuple[Leftover, ...] = (TYPESCRIPT_CACHE, AGENT_RULES)
#: What the root ``.gitignore`` of a project names once nothing is left to clean up.
IGNORED: tuple[str, ...] = tuple(line for leftover in LEFTOVERS for line in leftover.ignore)
COMMIT_MESSAGE = TYPESCRIPT_CACHE.commit_message


def keep_out_of_git(project_root: Path) -> Optional[str]:
    """Name every :data:`LEFTOVERS` kind in the project's root ``.gitignore`` and stop tracking what it is — in one
    commit of these paths only, without the pre-commit check (it would type-check a frontend for a cache file).
    ``None`` when there was nothing to do or it is done; otherwise why not, in the manager's words. Never raises.

    Someone else's change — anything staged that is not a leftover, or an unsaved edit of ``.gitignore`` — is never
    swept into this commit: then nothing happens and the reason says so. A staged leftover is this cleanup's own:
    Career Asistent's two were left staged by a killed commit (DEV-180) and kept the cleanup from ever running. Nor is
    a red proof the agent stayed on (DEV-173): its newest commit must stay the proof, or the cockpit would no longer
    know it for one — the next task, back on the working branch, does the cleanup."""
    try:
        if not (project_root / ".git").exists():
            return None
        gitignore = project_root / ".gitignore"
        lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
        work = [
            (leftover, tracked, [line for line in leftover.ignore if line not in lines])
            for leftover in LEFTOVERS
            for tracked in [leftover.tracked(project_root)]
        ]
        work = [(leftover, tracked, missing) for leftover, tracked, missing in work if tracked or missing]
        if not work:
            return None
        if _git(project_root, "log", "-1", "--format=%s").stdout.lower().startswith(ci_runs.PROOF_MARK):
            return None
        staged = [
            path for path in _git(project_root, "diff", "--cached", "--name-only", "-z").stdout.split("\0") if path
        ]
        foreign = [path for path in staged if not any(leftover.owns(project_root, path) for leftover in LEFTOVERS)]
        if foreign or _git(project_root, "diff", "--name-only", "--", ".gitignore").stdout.strip():
            return FOREIGN_CHANGES
        additions = "".join(
            f"{leftover.comment}\n" + "".join(f"{line}\n" for line in missing)
            for leftover, _t, missing in work
            if missing
        )
        if additions:
            text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
            separator = "" if not text or text.endswith("\n") else "\n"
            gitignore.write_text(f"{text}{separator}{additions}", encoding="utf-8")
            _git(project_root, "add", "--", ".gitignore")
        tracked = [path for _l, paths, _m in work for path in paths]
        if tracked:
            _git(project_root, "rm", "--cached", "-q", "--", *tracked)
        if _git(project_root, "diff", "--cached", "--quiet").returncode == 0:
            return None  # only leftovers that git had never committed — out of the index, nothing to commit
        message = f"chore: {' and '.join(leftover.what for leftover, _t, _m in work)} stay out of git"
        done = _git(project_root, "commit", "--no-verify", "-q", "-m", message)
        if done.returncode != 0:
            logger.warning("build leftovers commit failed in %s: %s", project_root, done.stderr.strip()[:300])
            return "pozostatky stavby sa z gitu nepodarilo odobrať — commit zlyhal"
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("build leftovers could not be kept out of git in %s: %s", project_root, exc)
        return "pozostatky stavby sa z gitu nepodarilo odobrať"
