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

import subprocess
from pathlib import Path

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


def _run(root: Path, args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    """Run ``git <args>`` inside ``root`` with a bounded timeout (never raises on nonzero)."""
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
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


def discard_all(source_path: str, *, timeout: int = 60) -> dict:
    """Discard ALL pending changes (tracked + untracked) → clean tree.

    ``git checkout -- .`` (revert tracked edits/deletes) + ``git clean -fd`` (remove
    untracked files/dirs). Destructive — the router gates this behind an explicit
    operator confirmation. Returns ``{ok: bool}`` (true when the tree ends up clean).
    """
    root = _project_root(source_path)
    _run(root, ["checkout", "--", "."], timeout=timeout)
    _run(root, ["clean", "-fd"], timeout=timeout)
    return {"ok": working_tree_status(source_path, timeout=timeout)["clean"]}
