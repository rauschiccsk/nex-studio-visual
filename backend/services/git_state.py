"""Git working-tree preflight for version founding (v4.0.25).

NEX Studio founds a version assuming a CLEAN, committed project baseline. When the
working tree is dirty (a build that crashed mid-way, or any out-of-band edit), the
pipeline agent only discovers the uncommitted work in Príprava and surfaces it as an
expert-level scope question a non-expert operator (Tibor/Nazar) cannot answer — the
project blocks and needs Dedo. This module lets the New-Version flow detect a dirty
tree UP FRONT and guide the operator to a clean baseline (commit / discard) before
founding, so the pipeline always starts from a known-committed state.

Mirrors :mod:`backend.services.nexshared` — takes the project's ``source_path`` (its
``/opt/projects/<slug>`` workspace) and shells out to ``git`` with a bounded timeout.

Security note: :func:`commit_all` stages only paths ``git status`` reports, and ``git status``
never reports an untracked ignored file. Every NEX Studio project gitignores ``.env`` (Create
Project, main CLAUDE.md §4), so secrets are never staged. This service never pushes — commits
stay local.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

#: Cap the surfaced file list so a huge dirty tree can't bloat the payload. The
#: ``dirty_count`` is always the EXACT total; ``truncated`` flags when the list was cut.
_MAX_FILES = 200


def _project_root(source_path: str) -> Path:
    """Resolve + validate a project's git workspace root.

    ``source_path`` is system-set (the project's ``/opt/projects/<slug>``), but a git
    command on the wrong path is worth guarding: the resolved path must be an existing
    directory that is itself a git repository (has a ``.git`` entry).
    """
    if not source_path:
        raise ValueError("Projekt nemá zdrojovú cestu.")
    root = Path(source_path).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError(f"Zdrojová cesta nie je git repozitár: {source_path}")
    return root


def workspace_is_git_repo(source_path: str) -> bool:
    """True when ``source_path`` is a directory that is itself a git repository.

    Lets a caller tell "there is no git history here" apart from "the git command failed", which the
    preflight needs: a workspace with no repo has NOTHING to guard, exactly like a project with no
    source path, and answering it with an error blocked founding behind a guard with nothing to guard.
    """
    if not source_path:
        return False
    root = Path(source_path).resolve()
    return root.is_dir() and (root / ".git").exists()


def _run(
    root: Path, args: list[str], *, timeout: int, c_locale: bool = False, stdin: Optional[str] = None
) -> subprocess.CompletedProcess:
    """Run ``git <args>`` inside ``root`` with a bounded timeout (never raises on nonzero).

    ``c_locale`` forces untranslated English output — needed only by the one call whose
    stdout is parsed as prose (``git clean -nd``). Off by default so git keeps reading the
    ambient locale for everything else (Slovak commit messages). ``stdin`` is fed to git.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "LC_ALL": "C"} if c_locale else None,
    )


def _run_over(root: Path, args: list[str], paths: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess:
    """``git <args>`` over exactly *paths*, each taken literally (no glob, no magic) and handed over on stdin, so a long
    list never hits the limit of a command line."""
    return _run(
        root,
        ["--literal-pathspecs", *args, "--pathspec-from-file=-", "--pathspec-file-nul"],
        timeout=timeout,
        stdin="\0".join(paths),
    )


def _excluded(exclude: Sequence[str]) -> list[str]:
    """*exclude* (git pathspec globs) as pathspec entries that leave those paths out."""
    return [f":(glob,exclude){pattern}" for pattern in exclude]


def _outside(exclude: Sequence[str]) -> list[str]:
    """The pathspec for the whole workspace except *exclude*; empty when nothing is excluded."""
    return ["--", ".", *_excluded(exclude)] if exclude else []


def _status_entries(
    root: Path, *, exclude: Sequence[str], timeout: int, every_file: bool = False
) -> list[tuple[str, str, Optional[str]]]:
    """``git status --porcelain -z`` outside *exclude* as ``(XY, path, source)`` entries.

    ``-z`` emits paths RAW, never quoted, and puts the source of a rename or a copy in the field
    after its entry — ``source`` carries it, ``None`` elsewhere. *every_file* names each untracked
    file on its own instead of its untracked folder.

    Raises:
        ValueError: ``git`` failed.
    """
    args = ["status", "--porcelain", "-z", *(["--untracked-files=all"] if every_file else []), *_outside(exclude)]
    proc = _run(root, args, timeout=timeout)
    if proc.returncode != 0:
        raise ValueError("git status zlyhal")
    fields = iter(proc.stdout.split("\0"))
    entries: list[tuple[str, str, Optional[str]]] = []
    for field in fields:
        if not field:
            continue
        xy = field[:2]
        source = next(fields, None) if set(xy) & {"R", "C"} else None
        entries.append((xy, field[3:], source))
    return entries


def working_tree_status(
    source_path: str, *, paths: Sequence[str] = (), exclude: Sequence[str] = (), timeout: int = 30
) -> dict:
    """Return the project's working-tree cleanliness for the founding preflight.

    ``{clean, dirty_count, files: [{code, path}], truncated}`` from ``git status
    --porcelain``. ``clean`` is the gate the New-Version flow checks: founding is
    allowed only when ``clean`` is true. *paths* limits the answer to those paths (DEV-164: the switch to Web asks only
    about the files it writes); empty = the whole workspace. *exclude* (git pathspec globs) leaves paths out —
    DEV-174: the files the cockpit writes itself (``workspace_owner.COCKPIT_WRITES``).

    Raises:
        ValueError: the source path is missing / not a git repo, or ``git`` failed.
    """
    root = _project_root(source_path)
    args = ["status", "--porcelain"]
    if exclude:
        args += ["--", *(paths or ["."]), *_excluded(exclude)]
    elif paths:
        args += ["--", *paths]
    proc = _run(root, args, timeout=timeout)
    if proc.returncode != 0:
        raise ValueError("git status zlyhal")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    files = [{"code": (ln[:2].strip() or ln[:2]), "path": ln[3:]} for ln in lines[:_MAX_FILES]]
    return {
        "clean": len(lines) == 0,
        "dirty_count": len(lines),
        "files": files,
        "truncated": len(lines) > _MAX_FILES,
    }


def commit_all(source_path: str, message: str, *, exclude: Sequence[str] = (), timeout: int = 60) -> dict:
    """Stage + commit the pending changes the guard shows, so the tree is clean before founding.

    The paths come from the same ``git status`` as :func:`working_tree_status` — outside
    *exclude* (git pathspec globs), DEV-203: the files the cockpit writes itself — and go to
    ``git add -A`` and ``git commit`` named one by one. DEV-303: a pathspec that names an ignored
    folder, even as an exception, makes ``git add`` refuse („The following paths are ignored by
    one of your .gitignore files“), so a project that ignores the cockpit's folders could never
    save. The commit names its paths, so an entry staged earlier outside them does not ride
    along. A "nothing to commit" result is a benign no-op (returns ``ok`` when the tree ends
    up clean), not an error.

    Returns ``{ok: bool, error?: str, note?: str}``.
    """
    root = _project_root(source_path)
    msg = (message or "").strip() or "chore: uloženie rozrobených zmien pred založením novej verzie"
    pending = _status_entries(root, exclude=exclude, timeout=timeout, every_file=True)
    # Only work still in the working tree needs staging, under the name it has now: naming a path that is neither in
    # the index nor on disk (the source of a staged rename, a staged deletion) makes ``git add`` refuse.
    unstaged = [path for xy, path, _ in pending if xy[1] != " "]
    if unstaged and _run_over(root, ["add", "-A"], unstaged, timeout=timeout).returncode != 0:
        return {"ok": False, "error": "git add zlyhal"}
    # The commit takes what is staged now — both names of a rename; a file added and then deleted is gone from both.
    staged = [
        path
        for xy, name, source in _status_entries(root, exclude=exclude, timeout=timeout, every_file=True)
        if xy[0] not in " ?"
        for path in (name, source)
        if path is not None
    ]
    commit = _run_over(root, ["commit", "-m", msg], staged, timeout=timeout) if staged else None
    if commit is None or commit.returncode != 0:
        # "nothing to commit" (already clean) is benign — verify by re-checking the tree.
        if working_tree_status(source_path, exclude=exclude, timeout=timeout)["clean"]:
            return {"ok": True, "note": "nothing-to-commit"}
        return {"ok": False, "error": "git commit zlyhal"}
    return {"ok": True}


def _untracked_paths(root: Path, *, exclude: Sequence[str], timeout: int) -> list[str]:
    """Untracked entries from ``git status --porcelain -z`` (``-z`` emits paths RAW, never quoted)."""
    return [path for xy, path, _ in _status_entries(root, exclude=exclude, timeout=timeout) if xy == "??"]


def _cleanable_paths(root: Path, *, exclude: Sequence[str], timeout: int) -> Optional[set[str]]:
    """What ``git clean -fd`` WOULD remove, asked via its own dry run. None when git failed.

    ``core.quotepath=false`` + the C locale keep the output raw + English so the
    ``Would remove `` prefix parses deterministically; anything that fails to parse simply
    stays out of the set, which makes the caller refuse rather than destroy.
    """
    args = ["-c", "core.quotepath=false", "clean", "-nd", *_outside(exclude)]
    proc = _run(root, args, timeout=timeout, c_locale=True)
    if proc.returncode != 0:
        return None
    prefix = "Would remove "
    cleanable: set[str] = set()
    for ln in proc.stdout.splitlines():
        if not ln.startswith(prefix):
            continue
        path = ln[len(prefix) :]
        # git still C-quotes a path containing a quote, a backslash or a newline even with
        # core.quotepath=false. Unquoting it correctly is not worth the risk here: an unparsed path
        # simply never joins the set, so the caller REFUSES instead of destroying — which is the safe
        # direction. The refusal now names the file (see discard_all), so the Manager can remove it
        # himself rather than facing a permanent, unexplained "cannot discard".
        if path.startswith('"'):
            continue
        cleanable.add(path)
    return cleanable


def discard_all(source_path: str, *, exclude: Sequence[str] = (), timeout: int = 60) -> dict:
    """Discard ALL pending changes (index + tracked + untracked) → clean tree.

    ``git restore --source=HEAD --staged --worktree`` (index AND working tree — plain
    ``git checkout -- .`` leaves STAGED changes behind, so the tree stayed dirty) + ``git clean
    -fd`` (untracked files/dirs). *exclude* (git pathspec globs) is left as it is by both —
    DEV-203: the files the cockpit writes itself, which ``git reset --hard`` used to take too.

    Fail-fast by construction: everything that could make the discard incomplete is checked
    BEFORE anything is destroyed, because the destruction is irreversible and "your work is
    gone, and by the way it failed" is the worst outcome this action can have. Preflight:

    * ``HEAD`` must resolve — with no commit there is no baseline to reset to;
    * every untracked entry must be one git says it would remove — ``git clean -fd``
      silently SKIPS nested git repositories, which would survive and leave the tree dirty.

    Destructive — the router gates this behind an explicit operator confirmation.
    Returns ``{ok: bool, error?: str}``.
    """
    root = _project_root(source_path)
    if _run(root, ["rev-parse", "--verify", "HEAD"], timeout=timeout).returncode != 0:
        return {"ok": False, "error": "Projekt nemá žiadny commit, ku ktorému by sa dalo vrátiť."}
    cleanable = _cleanable_paths(root, exclude=exclude, timeout=timeout)
    if cleanable is None:
        return {"ok": False, "error": "git clean zlyhal"}
    surviving = [p for p in _untracked_paths(root, exclude=exclude, timeout=timeout) if p not in cleanable]
    if surviving:
        shown = ", ".join(surviving[:5]) + (" …" if len(surviving) > 5 else "")
        return {"ok": False, "error": f"Tieto položky sa nedajú zahodiť, nič sa nezmazalo: {shown}"}

    # Preflight passed — only now destroy.
    scope = _outside(exclude) or ["--", "."]
    if _run(root, ["restore", "--source=HEAD", "--staged", "--worktree", *scope], timeout=timeout).returncode != 0:
        return {"ok": False, "error": "git restore zlyhal"}
    if _run(root, ["clean", "-fd", *scope], timeout=timeout).returncode != 0:
        return {"ok": False, "error": "git clean zlyhal"}
    if not working_tree_status(source_path, exclude=exclude, timeout=timeout)["clean"]:
        return {"ok": False, "error": "Pracovný strom je aj po zahodení zmenený."}
    return {"ok": True}
