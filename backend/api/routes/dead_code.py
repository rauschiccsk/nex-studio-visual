"""``GET /projects/{slug}/dead-code`` — the code nobody uses that the project's earlier versions left behind (DEV-156).

Verifikácia stops only what a version adds; what was there before the check is offered here as a cleanup the Manažér
starts himself, as a fast fix. Access as to the project. A sync route: the check runs git and a container, so it runs
in the worker threads.

Its result is kept per commit — the page asks on every visit, the code changes only with a new commit. A result of a
check that did not fully run is kept for a while only (:data:`RETRY_AFTER`), so a broken container is not run again on
every visit; and two visits at once wait for one check instead of starting two.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.dead_code import DeadCodeFindingRead, DeadCodeRead
from backend.services import claude_agent, dead_code

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Nepoužívaný kód"])

#: How many commits' results are kept — one per project is what the page needs.
_KEPT = 64
#: How long a result of a check that did not fully run is kept before the check is tried again (seconds).
RETRY_AFTER = 600
#: (slug, commit) → (result, until when it holds — ``None`` for as long as the commit is the project's).
_results: dict[tuple[str, str], tuple[DeadCodeRead, Optional[float]]] = {}
_running: dict[tuple[str, str], threading.Lock] = {}
_guard = threading.Lock()


def _head(slug: str) -> Optional[str]:
    proc = subprocess.run(
        ["git", "-C", str(claude_agent.PROJECTS_ROOT / slug), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _cleanup(slug: str, head: str) -> DeadCodeRead:
    try:
        report = dead_code.scan(claude_agent.PROJECTS_ROOT / slug, None, head)
    except dead_code.CheckNotRun as exc:
        return DeadCodeRead(findings=[], not_run=[str(exc)], brief=None)
    except Exception:  # noqa: BLE001 — the page of a project must stand when the check of its code fails
        logger.exception("dead code cleanup check failed (slug=%s)", slug)
        return DeadCodeRead(findings=[], not_run=["v kokpite nastala chyba (je zapísaná v jeho logu)"], brief=None)
    return DeadCodeRead(
        findings=[
            DeadCodeFindingRead(kind=f.kind, path=f.path, name=f.name, line=f.line, sentence=f.sentence())
            for f in report.old
        ],
        not_run=report.not_run,
        brief=dead_code.cleanup_brief(report.old) if report.old else None,
    )


def _kept(key: tuple[str, str]) -> Optional[DeadCodeRead]:
    found = _results.get(key)
    if found is None or (found[1] is not None and found[1] < time.monotonic()):
        return None
    return found[0]


@router.get("/projects/{slug}/dead-code", response_model=DeadCodeRead)
def project_dead_code(
    slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> DeadCodeRead:
    authz.assert_project_slug_access(db, current_user, slug)
    head = _head(slug)
    if head is None:
        return DeadCodeRead(findings=[], not_run=["projekt nemá čitateľný repozitár v gite"], brief=None)
    key = (slug, head)
    with _guard:
        running = _running.setdefault(key, threading.Lock())
    with running:
        result = _kept(key)
        if result is None:
            result = _cleanup(slug, head)
            if len(_results) >= _KEPT:
                _results.pop(next(iter(_results)))
            _results[key] = (result, time.monotonic() + RETRY_AFTER if result.not_run else None)
    with _guard:
        _running.pop(key, None)
    return result
