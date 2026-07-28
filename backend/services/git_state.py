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

Security note: :func:`commit_all` uses ``git add -A``, which RESPECTS ``.gitignore``.
Every NEX Studio project gitignores ``.env`` (Create Project, main CLAUDE.md §4), so
secrets are never staged. This service never pushes — commits stay local.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

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


def _run(root: Path, args: list[str], *, timeout: int, c_locale: bool = False) -> subprocess.CompletedProcess:
    """Run ``git <args>`` inside ``root`` with a bounded timeout (never raises on nonzero).

    ``c_locale`` forces untranslated English output — needed only by the one call whose
    stdout is parsed as prose (``git clean -nd``). Off by default so git keeps reading the
    ambient locale for everything else (Slovak commit messages).
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "LC_ALL": "C"} if c_locale else None,
    )


def working_tree_status(source_path: str, *, timeout: int = 30) -> dict:
    """Return the project's working-tree cleanliness for the founding preflight.

    ``{clean, dirty_count, files: [{code, path}], truncated}`` from ``git status
    --porcelain``. ``clean`` is the gate the New-Version flow checks: founding is
    allowed only when ``clean`` is true.

    Raises:
        ValueError: the source path is missing / not a git repo, or ``git`` failed.
    """
    root = _project_root(source_path)
    proc = _run(root, ["status", "--porcelain"], timeout=timeout)
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


def commit_all(source_path: str, message: str, *, timeout: int = 60) -> dict:
    """Stage + commit ALL pending changes so the tree is clean before founding.

    ``git add -A`` (respects ``.gitignore`` — see module note) then ``git commit``.
    A "nothing to commit" result is a benign no-op (returns ``ok`` when the tree ends
    up clean), not an error.

    Returns ``{ok: bool, error?: str, note?: str}``.
    """
    root = _project_root(source_path)
    msg = (message or "").strip() or "chore: uloženie rozrobených zmien pred založením novej verzie"
    add = _run(root, ["add", "-A"], timeout=timeout)
    if add.returncode != 0:
        return {"ok": False, "error": "git add zlyhal"}
    commit = _run(root, ["commit", "-m", msg], timeout=timeout)
    if commit.returncode != 0:
        # "nothing to commit" (already clean) is benign — verify by re-checking the tree.
        if working_tree_status(source_path, timeout=timeout)["clean"]:
            return {"ok": True, "note": "nothing-to-commit"}
        return {"ok": False, "error": "git commit zlyhal"}
    return {"ok": True}


def _untracked_paths(root: Path, *, timeout: int) -> list[str]:
    """Untracked entries from ``git status --porcelain -z`` (``-z`` emits paths RAW, never quoted)."""
    proc = _run(root, ["status", "--porcelain", "-z"], timeout=timeout)
    if proc.returncode != 0:
        raise ValueError("git status zlyhal")
    return [entry[3:] for entry in proc.stdout.split("\0") if entry[:2] == "??"]


def _cleanable_paths(root: Path, *, timeout: int) -> Optional[set[str]]:
    """What ``git clean -fd`` WOULD remove, asked via its own dry run. None when git failed.

    ``core.quotepath=false`` + the C locale keep the output raw + English so the
    ``Would remove `` prefix parses deterministically; anything that fails to parse simply
    stays out of the set, which makes the caller refuse rather than destroy.
    """
    proc = _run(root, ["-c", "core.quotepath=false", "clean", "-nd"], timeout=timeout, c_locale=True)
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


def discard_all(source_path: str, *, timeout: int = 60) -> dict:
    """Discard ALL pending changes (index + tracked + untracked) → clean tree.

    ``git reset --hard HEAD`` (index AND working tree — plain ``git checkout -- .`` leaves
    STAGED changes behind, so the tree stayed dirty) + ``git clean -fd`` (untracked
    files/dirs).

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
    cleanable = _cleanable_paths(root, timeout=timeout)
    if cleanable is None:
        return {"ok": False, "error": "git clean zlyhal"}
    surviving = [p for p in _untracked_paths(root, timeout=timeout) if p not in cleanable]
    if surviving:
        shown = ", ".join(surviving[:5]) + (" …" if len(surviving) > 5 else "")
        return {"ok": False, "error": f"Tieto položky sa nedajú zahodiť, nič sa nezmazalo: {shown}"}

    # Preflight passed — only now destroy.
    if _run(root, ["reset", "--hard", "HEAD"], timeout=timeout).returncode != 0:
        return {"ok": False, "error": "git reset zlyhal"}
    if _run(root, ["clean", "-fd"], timeout=timeout).returncode != 0:
        return {"ok": False, "error": "git clean zlyhal"}
    if not working_tree_status(source_path, timeout=timeout)["clean"]:
        return {"ok": False, "error": "Pracovný strom je aj po zahodení zmenený."}
    return {"ok": True}
