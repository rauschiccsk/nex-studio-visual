"""Whether Poradca can run — and if not, exactly why (ICCINT-167).

The same pattern as the readiness of the isolated build (:func:`build_sandbox.preflight`): a condition that cannot
be met should be visible before the first question runs into it. The screen uses it to grey out the question field
with a reason; ``/health`` shows it to operations.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Optional

from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.schemas.poradca import PoradcaStatus
from backend.services import build_sandbox
from backend.services.poradca import attachments, runner, sandbox

_CACHE_SECONDS = 30
_cache: Optional[tuple[float, list[str]]] = None


def problems() -> list[str]:
    """Unmet run conditions — sentences for the human. An empty list = Poradca can run."""
    global _cache
    now = time.time()
    if _cache is not None and now - _cache[0] < _CACHE_SECONDS:
        return list(_cache[1])
    found: list[str] = []
    # Poradca has no path outside the container, so the build isolation switch (BUILD_SANDBOX) does not turn it off —
    # the container conditions are checked here, each separately.
    image = build_sandbox.sandbox_image()
    if shutil.which("docker") is None:
        found.append("program docker nie je v kokpite k dispozícii — kontajner Poradcu sa nedá spustiť")
    else:
        try:
            proc = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", image],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode != 0:
                found.append(f"obraz kontajnera {image} na tomto stroji nie je")
        except (OSError, subprocess.SubprocessError):
            found.append(f"obraz kontajnera {image} sa nedá overiť")
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        found.append("chýba prihlásenie Claude (CLAUDE_CODE_OAUTH_TOKEN) — kontajner by sa neprihlásil")
    if not os.path.isdir(sandbox._CLAUDE_BIN_DIR):
        found.append(f"chýba priečinok s programom claude ({sandbox._CLAUDE_BIN_DIR})")
    data = sandbox.data_dir()
    if not data.is_dir():
        found.append(f"chýba priečinok Poradcu {data} — v PROD compose kokpitu má byť pripojený na tej istej ceste")
    elif not os.access(data, os.W_OK):
        found.append(f"do priečinka Poradcu {data} sa nedá zapisovať")
    if not runner._CHARTER.is_file():
        found.append("chýba charta Poradcu (templates/poradca-charter.md)")
    _cache = (now, found)
    return list(found)


def status(db: Session) -> PoradcaStatus:
    found = problems()
    return PoradcaStatus(
        ready=not found,
        problems=found,
        running=runner._slots.active,
        max_concurrent=runner.max_concurrent(db),
        attachment_max_bytes=settings.poradca_attachment_max_bytes,
        attachments_max_count=settings.poradca_attachments_max_count,
        attachments_max_total_bytes=settings.poradca_attachments_max_total_bytes,
        attachment_types=sorted(attachments.IMAGE_TYPES),
    )
