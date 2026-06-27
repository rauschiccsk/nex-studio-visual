"""Service layer for the Fast-Fix Lane ("Rýchla oprava"; F-009, CR-NS-094; v2 short path CR-V2-028).

The Fast-Fix Lane is a lightweight flow for small, obvious fixes that do not warrant a full version
build. In v2 it runs the SHORT path (``FAST_FIX_STAGE_ORDER`` = priprava → programovanie → verifikacia
→ done): the Manažér's directive IS the brief (no heavy Návrh, no per-task Auditor), the AI Agent fixes
it self-checking, a LIGHT focused Auditor check (fix works + no regression) verifies it, and the lane
STOPS at the verified/Hotovo boundary — it does NOT auto-deploy (OQ-3 resolved; deploy is the normal
manual per-customer Nasadiť in the UAT/PROD tabs, CR-V2-027). This module owns the two pieces of
plumbing the orchestrator does NOT:

* :func:`create_patch_version` — derive the next PATCH version (semver ``vX.Y.Z → vX.Y.Z+1`` from
  the project's latest version) and create it. The caller then starts a ``fast_fix`` pipeline on it.
* :func:`ensure_build_task` — materialize the ONE minimal Task (Epic → Feat → Task) from the Manažér's
  directive carried in the kickoff message, so the v2 self-checking Programovanie loop has a task to run.
  Re-targeted off the v1 per-task-audited loop onto the v2 short path (CR-V2-028). Idempotent.

Design notes:
    * No dependency on :mod:`backend.services.orchestrator` — the orchestrator imports THIS module
      (one direction), so the pipeline ``start`` / stage routing stays in the orchestrator and the
      version + task plumbing stays here.
    * Like the other services, methods accept ``db: Session`` first, only ``flush()`` (commit is the
      router's responsibility), and signal errors via :class:`ValueError`.
"""

from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineMessage
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.epic import EpicCreate
from backend.schemas.feat import FeatCreate
from backend.schemas.task import TaskCreate
from backend.schemas.version import VersionCreate
from backend.services import epic as epic_service
from backend.services import feat as feat_service
from backend.services import task as task_service
from backend.services import version as version_service

# Optional leading ``v`` + a strict ``major.minor.patch`` core. A pre-release / build suffix
# (``-rc1`` / ``+meta``) is tolerated for parsing but dropped from the bumped result.
_SEMVER = re.compile(r"^(?P<prefix>v?)(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")


def _parse_semver(version_number: str) -> Optional[tuple[str, int, int, int]]:
    """``(prefix, major, minor, patch)`` for a ``[v]X.Y.Z[...]`` string, or ``None`` if unparseable."""
    m = _SEMVER.match(version_number.strip())
    if m is None:
        return None
    return m.group("prefix"), int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


def bump_patch(version_number: str) -> str:
    """Return the next PATCH version for ``version_number`` (``vX.Y.Z → vX.Y.Z+1``).

    Preserves an optional leading ``v`` and drops any pre-release / build suffix.

    Raises:
        ValueError: If ``version_number`` is not a parseable ``[v]X.Y.Z`` semver.
    """
    parsed = _parse_semver(version_number)
    if parsed is None:
        raise ValueError(f"Cannot bump non-semver version_number: {version_number!r}")
    prefix, major, minor, patch = parsed
    return f"{prefix}{major}.{minor}.{patch + 1}"


def latest_semver_version(db: Session, project_id: UUID) -> Version:
    """Return the project's highest version by SEMVER ordering (NOT lexicographic).

    ``version_service.list_versions`` orders by ``version_number`` lexicographically, which is wrong
    for semver (``0.10.0`` < ``0.9.0`` as strings). The PATCH bump must anchor on the true semver max,
    so this picks the version with the greatest ``(major, minor, patch)`` among parseable ones.

    Raises:
        ValueError: If the project has no semver-parseable version to patch from.
    """
    versions = version_service.list_versions(db, project_id)
    best: Optional[Version] = None
    best_key: Optional[tuple[int, int, int]] = None
    for v in versions:
        parsed = _parse_semver(v.version_number)
        if parsed is None:
            continue
        _, major, minor, patch = parsed
        key = (major, minor, patch)
        if best_key is None or key > best_key:
            best, best_key = v, key
    if best is None:
        raise ValueError(f"Project {project_id} has no semver version to patch from (Fast-Fix needs a base version)")
    return best


def create_patch_version(db: Session, *, project_id: UUID, user_id: UUID) -> Version:
    """Create the next PATCH version for a Fast-Fix (``vX.Y.Z+1`` from the project's semver max).

    The version is created with the default ``planned`` status and a ``name`` marking it as a
    fast-fix patch; the caller then starts a ``fast_fix`` pipeline on it.

    Raises:
        ValueError: If the project has no semver base version, or the bumped version already exists.
    """
    base = latest_semver_version(db, project_id)
    next_number = bump_patch(base.version_number)
    return version_service.create(
        db,
        project_id,
        VersionCreate(version_number=next_number, name="Rýchla oprava"),
        user_id,
    )


def kickoff_directive(db: Session, version_id: UUID) -> Optional[str]:
    """The Manažér directive carried in the version's kickoff message payload (set by the orchestrator
    ``start`` for a ``fast_fix`` flow), or ``None``.

    The fast-fix kickoff is recorded by ``apply_action('start')`` in the v2 first phase: ``stage='priprava'``,
    ``author='manazer'``, ``kind='kickoff'``, ``payload.directive=<directive>`` (CR-V2-009 collapsed the v1
    ``kickoff``/``director`` tokens — both are rejected by the v2 DB CHECK). :func:`ensure_build_task` reads
    this so the Manažér's directive becomes the single fast-fix Task's brief on the v2 short path (CR-V2-028)."""
    msg = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "priprava",
            PipelineMessage.kind == "kickoff",
            PipelineMessage.author == "manazer",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if msg is None or not msg.payload:
        return None
    directive = msg.payload.get("directive")
    return directive if isinstance(directive, str) and directive.strip() else None


def _title_from_directive(directive: Optional[str]) -> str:
    """A short task title from the directive: its first non-empty line, trimmed to fit ``title`` (≤500)."""
    if directive:
        first_line = next((ln.strip() for ln in directive.splitlines() if ln.strip()), "").strip()
        if first_line:
            return first_line[:200]
    return "Rýchla oprava"


def ensure_build_task(db: Session, version_id: UUID) -> Task:
    """Materialize the ONE minimal Task for a Fast-Fix version (Epic → Feat → Task) — idempotent.

    The Manažér's directive (in the kickoff message) IS the task brief: it becomes the Task's
    ``description`` (full text) and a trimmed first line is the title. ``task_type`` defaults to
    ``backend`` (a neutral default — the directive guides the AI Agent to the real layer; the field
    only drives FE display + the optional checklist, left unset). If a Task already exists for the
    version (a re-entry into build), the existing first todo/any task is returned untouched.

    Called by the orchestrator at the START of the fast-fix Programovanie round (CR-V2-028): the v2 short
    path skips Návrh, so this is where the single task is created for the AI Agent's self-checking loop —
    the v2 replacement for the v1 per-task-audited build loop's plan materialization.

    Raises:
        ValueError: If the version does not exist.
    """
    version = db.get(Version, version_id)
    if version is None:
        raise ValueError(f"Version {version_id} not found")

    # Idempotent: if the version already has any Task, reuse it (a re-dispatch into build).
    existing = db.execute(
        select(Task)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .where(Epic.version_id == version_id)
        .order_by(Task.number.asc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    directive = kickoff_directive(db, version_id)
    epic = epic_service.create(
        db,
        EpicCreate(project_id=version.project_id, version_id=version_id, title="Rýchla oprava"),
    )
    feat = feat_service.create(
        db,
        FeatCreate(epic_id=epic.id, title="Rýchla oprava", description=directive or ""),
    )
    return task_service.create(
        db,
        TaskCreate(
            feat_id=feat.id,
            title=_title_from_directive(directive),
            description=directive or "",
            task_type="backend",
        ),
    )
