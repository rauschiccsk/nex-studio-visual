"""Everything the cockpit writes into the Znalostná báza belongs to its owner, not to root (DEV-111).

The backend runs as root. A file it wrote — or replaced through a temp file, which makes a new file — and every
directory it created belonged to root, and the owner of the Znalostná báza could then no longer change or replace it in
a terminal (13.10.2026 on ANDROS: the port registry, rewritten 09.10.2026). Git already runs as the owner
(:mod:`backend.services.kb_git`, through :func:`owner`). Every write of the cockpit into the Znalostná báza goes through
:func:`write_text` — the one place that hands the result over; no second copy of the rule.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def owner(root: Path) -> tuple[int, int]:
    """Who owns the Znalostná báza — the uid and gid of its root directory."""
    st = Path(root).stat()
    return st.st_uid, st.st_gid


def _missing_dirs(directory: Path) -> list[Path]:
    """The directories a ``mkdir(parents=True)`` of ``directory`` creates, outermost first."""
    missing: list[Path] = []
    for candidate in [Path(directory), *Path(directory).parents]:
        if candidate.exists():
            break
        missing.append(candidate)
    return missing[::-1]


def hand_over(root: Path, paths: Iterable[Path]) -> None:
    """Give ``paths`` to the owner of the Znalostná báza — only when the cockpit runs as root and the owner is not."""
    if os.geteuid() != 0:
        return
    uid, gid = owner(root)
    if uid == 0:
        return
    for path in paths:
        os.chown(path, uid, gid, follow_symlinks=False)


def write_text(root: Path, target: Path, text: str) -> None:
    """Write ``target`` through a temp file beside it (never half-written); it and new folders go to the owner."""
    target = Path(target)
    created = _missing_dirs(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():  # os.replace failed — no half-written temp file stays behind
            tmp.unlink()
    hand_over(root, [*created, target])
