"""DEV-58 — the environment of a project's release check (``release_smoke_test.sh``) in Verifikácia.

The previerka of Dedo Home on 04.10.2026 ran the cockpit's real engine over a copy of the project and found the
script's environment wrong in three ways — this module and :func:`orchestrator._run_acceptance_script` close them:

* **C1** — the script ran in the cockpit's ``/app``, not in the project (``lstat /app/Dockerfile.test``): it now
  runs in the project's root;
* **C2** — its temporary files (``mktemp -d``) lay in the cockpit container's ``/tmp``, which the Docker daemon
  does not see: a script mounting them into a container (``docker run -v``) found the mount empty. It now gets its
  own ``TMPDIR`` under :func:`scratch_root`, a folder mounted into the backend at the SAME path as on the host;
* **C4** — nothing bounded what it wrote: the project's tests filled ANDROS's root disk (~550 GB, 04.10.2026
  21:00–21:07 UTC). :class:`DiskGuard` stops it past a budget or below a floor of free space, with the sentence
  why.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.config.settings import settings

#: How often the disk guard looks at the free space while the check runs (seconds).
DISK_GUARD_INTERVAL = 5.0


class ScratchUnavailable(RuntimeError):
    """The scratch folder cannot be given to the check — Verifikácia fails with the reason, never runs blind."""


def scratch_root() -> Path:
    return Path(settings.release_smoke_tmp_root)


def new_scratch() -> Path:
    """A fresh folder for one run of the check, writable by any user (a script may mount it into a container that
    runs as someone else). The root is NOT created here: when it is missing, the PROD compose lacks its mount and
    a folder made inside the backend would be exactly the invisible ``/tmp`` this replaces.

    Raises:
        ScratchUnavailable: the root does not exist, or the folder cannot be made.
    """
    root = scratch_root()
    if not root.is_dir():
        raise ScratchUnavailable(
            f"Dočasný priečinok previerky {root} neexistuje — v PROD compose kokpitu chýba jeho pripojenie "
            "(rovnaká cesta na hostiteľovi aj v backende). Previerka sa bez neho nespustí."
        )
    folder = root / uuid.uuid4().hex
    try:
        folder.mkdir()
        os.chmod(folder, 0o1777)
    except OSError as exc:
        raise ScratchUnavailable(f"Dočasný priečinok previerky sa nedá pripraviť ({exc}).") from exc
    return folder


def discard(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def sweep() -> int:
    """At backend start: remove what runs interrupted by a restart left behind (a run lives in the backend's
    process, so none is in flight at start). Returns how many folders went. Never raises."""
    root = scratch_root()
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        shutil.rmtree(child, ignore_errors=True)
        removed += 0 if child.exists() else 1
    return removed


def _gb(size: int) -> str:
    return f"{size / 1024**3:.1f}".replace(".", ",")


@dataclass
class DiskGuard:
    """Watches the free space of the server's disk (the scratch folder lies on it, as does Docker's data)."""

    path: Path
    start_free: int
    budget: int
    floor: int

    @classmethod
    def start(cls, path: Path) -> "DiskGuard":
        return cls(
            path=path,
            start_free=shutil.disk_usage(path).free,
            budget=settings.release_smoke_disk_budget_bytes,
            floor=settings.release_smoke_min_free_bytes,
        )

    def exceeded(self) -> Optional[str]:
        """The sentence why the check must stop now — or ``None`` while it may go on."""
        free = shutil.disk_usage(self.path).free
        taken = self.start_free - free
        if taken > self.budget:
            return (
                f"Previerka zastavená strážcom disku: od začiatku zabrala na disku servera {_gb(taken)} GB, viac "
                f"než dovolených {_gb(self.budget)} GB (voľné miesto kleslo z {_gb(self.start_free)} na "
                f"{_gb(free)} GB). Skúšky projektu nesmú zaplniť server — over, čo v nich toľko zapisuje."
            )
        if free < self.floor:
            return (
                f"Previerka zastavená strážcom disku: na disku servera ostáva len {_gb(free)} GB voľných, menej "
                f"než hranica {_gb(self.floor)} GB. Uvoľni miesto na serveri, potom spusti Verifikáciu znova."
            )
        return None
