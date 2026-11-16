"""DEV-413 — the documentation of the cockpit's other projects, for the agents a project's own walls kept from it.

16.11.2026 the Staviteľ of NEX HelpDesk stopped in Príprava and asked the Manažér to attach the description of NEX
Knowledge's interface for other applications — a document the cockpit has (``nex-knowledge/docs/API_PRE_APLIKACIE.md``)
and the Projektant had read while shaping the brief. The project's Poradca, asked how to answer, sent the Manažér to
download the file by hand and, reading only the database schema in the Knowledge Base, claimed a discrepancy that is
not there. Director: „Riešením nie je, že ty budeš vkladať dokumenty, ktoré sú k dispozícii v kokpite. Prečo nemôže
Staviteľ pozrieť čo potrebuje?“ — and for the Poradca too: „Áno“.

What a project's documentation is stays ONE rule, the Projektant's (DEV-345, :mod:`backend.services.doc_ages`): its
``README.md`` and every ``.md`` under ``docs/``, never a file named like a secret, never code, never the attachments
in ``private/``. :func:`readable` is that rule for one path; the Projektant's and the Poradca's tool read through it,
and :func:`snapshot` copies through it for the Staviteľ — whose sandbox gets files, not tools.

The Staviteľ gets a COPY, made before each sandboxed turn and removed after it, mounted read-only at
:data:`CONTAINER_DIR`: mounting the other projects' ``docs/`` would hand it everything else that lies there (e-mails,
exports, configuration — measured 16.11.2026: an ``.eml``, ``.csv``, ``.json`` and ``.yml`` files), and a copy can
hold exactly what the rule lets through. Its folder is on the host at the same path the backend sees
(:attr:`Settings.project_docs_dir`), because the sandbox's ``--mount`` source is resolved by the docker daemon.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from backend.config.settings import settings
from backend.db.models.projects import Project
from backend.db.session import SessionLocal
from backend.services import doc_ages, secret_files

logger = logging.getLogger(__name__)

#: Where a sandboxed turn finds the other projects' documentation: ``<CONTAINER_DIR>/<slug>/docs/…``, read-only.
CONTAINER_DIR = "/opt/project-docs"

#: A turn's copy is named by the turn's token (:func:`build_sandbox.turn_token`) — nothing else ever lands in a path.
_TOKEN_RE = re.compile(r"^[0-9a-f]{6,32}$")

#: A copy older than this belongs to a turn that is long gone (a crash before its cleanup) and is swept.
_STALE_SECONDS = 24 * 3600

#: The sentence a sandboxed turn's brief carries when the copy is mounted — a running build reads its charter only at
#: the first call of its session, so the rule reaches it here too.
TURN_NOTE = (
    f"Dokumentácia ostatných projektov kokpitu (README.md a súbory .md z docs/) je len na čítanie v "
    f"`{CONTAINER_DIR}/<slug projektu>/`. Keď zadanie stojí na inom projekte, prečítaj si jeho dokumentáciu tam a "
    "Manažéra o ňu nežiadaj."
)


def document_path(root: str, wanted: str) -> Optional[tuple[str, str]]:
    """``(real path, path in the project)`` when ``wanted`` (relative to ``root``) names a place of the project's
    documentation — ``README.md`` or a ``.md`` under ``docs/`` whose real path stays there (a link out of the project
    is not its document); ``None`` otherwise. Whether the document is there is :func:`readable`'s question."""
    root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, wanted.lstrip("/")))
    rel = os.path.relpath(full, root)
    inside_docs = full.startswith(os.path.join(root, doc_ages.DOCS_DIR) + os.sep)
    if not (inside_docs or rel in doc_ages.ROOT_DOCS) or not rel.endswith(".md"):
        return None
    return full, rel


def readable(root: str, wanted: str) -> Optional[str]:
    """The real path of the project's document ``wanted`` — or ``None`` when it is not one: not a place of its
    documentation (:func:`document_path`), not a regular file, or named like a secret."""
    found = document_path(root, wanted)
    if found is None:
        return None
    full, _rel = found
    if secret_files.SECRET_FILE_RE.match(os.path.basename(full)) or not os.path.isfile(full):
        return None
    return full


def _base() -> Path:
    return Path(settings.project_docs_dir)


def _other_slugs(own_slug: str) -> list[str]:
    """Every project of the cockpit but this one — the projects the Projektant's tool reads (DEV-345)."""
    with SessionLocal() as db:
        slugs = db.execute(select(Project.slug).order_by(Project.slug)).scalars().all()
    return [slug for slug in slugs if slug and slug != own_slug]


def _sweep_stale(base: Path) -> None:
    now = time.time()
    for entry in base.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink() and now - entry.stat().st_mtime > _STALE_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def snapshot(own_slug: str, token: str) -> Optional[str]:
    """Copy the documentation of every OTHER project of the cockpit for one sandboxed turn; the copy's host path.

    ``None`` — logged — when the copy cannot be made (its folder is missing, the database cannot be asked, a write
    fails): the turn then runs without it, as before DEV-413, rather than not at all."""
    from backend.services.claude_agent import PROJECTS_ROOT

    if not _TOKEN_RE.match(token):
        raise ValueError(f"turn token {token!r} is not a turn token")
    base = _base()
    if not base.is_dir():
        logger.warning("project docs: %s does not exist — the %s turn runs without them", base, own_slug)
        return None
    target = base / f"turn-{token}"
    try:
        _sweep_stale(base)
        slugs = _other_slugs(own_slug)
        target.mkdir(mode=0o755)
        copied = 0
        for slug in slugs:
            root = os.path.realpath(PROJECTS_ROOT / slug)
            if not os.path.isdir(root):
                continue
            for rel in doc_ages.documents(root):
                full = readable(root, rel)
                if full is None:
                    continue
                dest = target / slug / rel
                dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                shutil.copyfile(full, dest, follow_symlinks=True)
                os.chmod(dest, 0o644)
                copied += 1
        for dirpath, _dirs, _files in os.walk(target):
            os.chmod(dirpath, 0o755)
    except Exception:  # noqa: BLE001 — a missing copy must never cost the turn
        logger.warning("project docs: the copy for the %s turn failed — it runs without it", own_slug, exc_info=True)
        shutil.rmtree(target, ignore_errors=True)
        return None
    logger.info("project docs: %d documents of %d projects for the %s turn", copied, len(slugs), own_slug)
    return str(target)


def is_snapshot(path: str) -> bool:
    """True for a turn's copy made by :func:`snapshot` — the one thing the sandbox may mount from here."""
    base = os.path.realpath(_base())
    real = os.path.realpath(path)
    return os.path.dirname(real) == base and bool(re.match(r"^turn-[0-9a-f]{6,32}$", os.path.basename(real)))


def remove(path: Optional[str]) -> None:
    """Remove a turn's copy; anything else is left alone."""
    if path and is_snapshot(path):
        shutil.rmtree(path, ignore_errors=True)
