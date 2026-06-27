"""Service layer for :class:`~backend.db.models.versions.Version`.

Provides the synchronous CRUD surface used by API routers, plus the two
lifecycle helpers required by DESIGN.md §4.0 Version Lifecycle Rules:
:func:`release` (the release gate) and :func:`auto_activate`
(``planned → active`` on the first ``in_progress`` epic).

All methods accept ``db: Session`` as the first argument and only ever
call ``session.flush()`` — transaction commit is the router's
responsibility. Errors are signalled via :class:`ValueError` so the
router can translate them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.13 ``versions`` table, §2.6 Version
Management and §4.0 Version Lifecycle Rules, and
:mod:`backend.db.models.versions`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` is an immutable foreign key — a version belongs to
      exactly one project for its lifetime. :class:`VersionUpdate`
      deliberately omits it and :func:`update`'s ``allowed_fields``
      allow-list enforces that contract defensively.
    * ``status`` is constrained by the ``ck_versions_status`` DB CHECK
      (``planned | active | released``). The Pydantic
      :data:`~backend.schemas.version.VersionStatus` literal mirrors the
      DB constraint, so the service does not revalidate — if an invalid
      value ever reaches the service (e.g. a bypassed schema) the DB
      CHECK rejects it on flush.
    * ``UNIQUE(project_id, version_number)`` is re-validated defensively
      before flush in :func:`create` so concurrent creates surface as a
      clean :class:`ValueError` instead of a raw
      :class:`~sqlalchemy.exc.IntegrityError`.
    * Inbound FKs from ``epics.version_id`` and ``bugs.version_id`` use
      ``ON DELETE RESTRICT`` — deletion is not exposed here because
      DESIGN.md §4.0 Rule 6 states a version may not be deleted while
      any EPIC or BUG still references it. This task does not ship a
      :func:`delete` helper; the router handles 409 via a dedicated code
      path in a follow-up task.
    * :func:`list_versions` orders by ``version_number DESC`` per
      DESIGN.md §2.6 ``GET /projects/{id}/versions`` and attaches three
      aggregate counts (``epic_count``, ``epics_done``, ``bug_count``)
      to each returned ORM instance so
      :class:`~backend.schemas.version.VersionRead` can serialise them
      via ``from_attributes=True``.
    * :func:`get_by_id` eager-loads ``epics`` and ``bugs`` with
      ``selectinload`` — DESIGN.md §2.6 ``GET /versions/{id}`` returns
      the version detail "with all EPICs and BUGs grouped", so the
      router needs both collections populated in one trip.
    * :func:`release` implements the release gate from
      DESIGN.md §4.0 Rule 5 — all EPICs in the version must have
      ``status = 'done'`` or the transition is rejected with the list of
      blocking EPIC IDs embedded in the :class:`ValueError` message (the
      router maps this to HTTP 409).
    * :func:`auto_activate` implements DESIGN.md §4.0 Rule 4 — when the
      first EPIC in a version transitions to ``in_progress``, the
      version auto-transitions ``planned → active``. Called from the
      Epic service (Task 9.4). A no-op for versions already in
      ``active`` or ``released`` (status transitions flow only forward
      per Rule 3).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.db.models.bugs import Bug
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version
from backend.schemas.version import VersionCreate, VersionUpdate
from backend.services import backlog as backlog_service

#: Filesystem root every project workspace lives under (mirrors
#: :data:`backend.services.project_specs.PROJECTS_ROOT`).
_PROJECTS_ROOT = Path("/opt/projects")


def list_versions(db: Session, project_id: UUID) -> list[Version]:
    """Return every version for ``project_id``, ordered by ``version_number DESC``.

    Matches the DESIGN.md §2.6 contract for ``GET
    /projects/{id}/versions``: the most-recently-numbered release sits
    at the top of the list (strings are compared lexicographically —
    callers are expected to use zero-padded semver where this matters).

    Each returned :class:`Version` instance carries three transient
    aggregate attributes populated from correlated subqueries:

        * ``epic_count`` — total number of EPICs assigned to the version.
        * ``epics_done`` — number of those EPICs with ``status = 'done'``.
        * ``bug_count`` — total number of BUGs assigned to the version.

    These match the ``VersionRead`` schema fields
    (``from_attributes=True``), so the router can serialise the result
    directly with ``VersionRead.model_validate(version)``.

    Args:
        db: Active SQLAlchemy session.
        project_id: Project filter — restrict to versions belonging to
            this project.

    Returns:
        List of :class:`Version` instances with the aggregate counts
        attached as transient attributes.
    """
    epic_count_col = (
        select(func.count(Epic.id)).where(Epic.version_id == Version.id).correlate(Version).scalar_subquery()
    )
    epics_done_col = (
        select(func.count(Epic.id))
        .where(Epic.version_id == Version.id, Epic.status == "done")
        .correlate(Version)
        .scalar_subquery()
    )
    bug_count_col = select(func.count(Bug.id)).where(Bug.version_id == Version.id).correlate(Version).scalar_subquery()

    stmt = (
        select(Version, epic_count_col, epics_done_col, bug_count_col)
        .where(Version.project_id == project_id)
        .order_by(Version.version_number.desc())
    )

    result: list[Version] = []
    for version, epic_count, epics_done, bug_count in db.execute(stmt).all():
        version.epic_count = int(epic_count or 0)
        version.epics_done = int(epics_done or 0)
        version.bug_count = int(bug_count or 0)
        result.append(version)
    return result


def get_by_id(db: Session, version_id: UUID) -> Version:
    """Return a single version by primary key with ``epics`` and ``bugs`` eager-loaded.

    DESIGN.md §2.6 ``GET /versions/{id}`` specifies "Version detail with
    all EPICs and BUGs grouped", so the service eagerly loads both
    relationships via ``selectinload`` to avoid per-row N+1 queries in
    the router's response-building step.

    Raises:
        ValueError: If no version with the supplied ``version_id``
            exists. The router converts this to an HTTP 404 response.
    """
    stmt = (
        select(Version)
        .where(Version.id == version_id)
        .options(
            selectinload(Version.epics),
            selectinload(Version.bugs),
        )
    )
    version = db.execute(stmt).scalar_one_or_none()
    if version is None:
        raise ValueError(f"Version {version_id} not found")
    return version


def _get_by_project_and_version_number(
    db: Session,
    project_id: UUID,
    version_number: str,
) -> Optional[Version]:
    """Internal helper — look up a version by the unique
    ``(project_id, version_number)`` pair."""
    stmt = select(Version).where(
        Version.project_id == project_id,
        Version.version_number == version_number,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(
    db: Session,
    project_id: UUID,
    data: VersionCreate,
    user_id: UUID,  # noqa: ARG001 — accepted for API parity; not persisted yet.
) -> Version:
    """Create a new version for ``project_id``.

    ``status`` defaults to ``planned`` via the DB ``server_default`` —
    callers cannot override it here; status only advances through
    :func:`auto_activate` and :func:`release` (DESIGN.md §4.0 Rule 3:
    forward-only transitions).

    The ``(project_id, version_number)`` pair is re-validated against
    the DB unique constraint before flush so a race between concurrent
    creates on the same project surfaces as a clean :class:`ValueError`
    (HTTP 409 at the router layer) rather than a raw
    :class:`~sqlalchemy.exc.IntegrityError`.

    The ``user_id`` parameter is accepted to match the service contract
    used by the forthcoming ``POST /projects/{id}/versions`` router
    (auth'd user performing the creation) and to leave room for a
    future ``created_by`` audit column; the current
    :class:`~backend.db.models.versions.Version` model does not yet
    persist it, so the parameter is currently unused.

    Args:
        db: Active SQLAlchemy session.
        project_id: Project the version belongs to.
        data: Validated creation payload.
        user_id: Authenticated user performing the creation — reserved
            for a future ``created_by`` column; not persisted today.

    Returns:
        The newly created and flushed :class:`Version` with its
        server-generated ``id``, ``status`` (``planned``),
        ``created_at`` and ``updated_at`` populated.

    Raises:
        ValueError: If another version already uses the same
            ``(project_id, version_number)`` pair.
    """
    if _get_by_project_and_version_number(db, project_id, data.version_number) is not None:
        raise ValueError(
            f"Version with project_id={project_id} and version_number={data.version_number!r} already exists"
        )

    version = Version(
        project_id=project_id,
        version_number=data.version_number,
        name=data.name,
        description=data.description,
        target_date=data.target_date,
    )
    db.add(version)
    db.flush()
    return version


def write_zadanie(db: Session, version_id: UUID, content: str) -> str:
    """Persist a version's free-text **Zadanie** to ``customer-requirements.md`` (CR-V2-024).

    The New-Version flow (design §4.3) lets the Manažér enter the brief as free text and saves it
    to ``docs/specs/versions/v<N>/customer-requirements.md`` inside the project workspace. The
    Príprava phase (CR-V2-010) reads exactly this file when the Manažér clicks "Spustiť tvorbu
    špecifikácie", so the write path is computed to MATCH the orchestrator's
    ``_version_spec_rel`` convention (``docs/specs/versions/v{version_number}``) — the two must
    never diverge or the AI Agent reads an empty Zadanie.

    Unlike the edit-only ``project_specs`` browser write, this is a deliberate CREATE-or-overwrite:
    the version's spec directory does not exist yet at this point in the flow, so parent directories
    are created.

    Returns the repo-relative path that was written (e.g.
    ``docs/specs/versions/v0.1.0/customer-requirements.md``).

    Raises:
        ValueError: the version (or its project) does not exist. The router maps this to HTTP 404.
    """
    row = db.execute(
        select(Version.version_number, Project.slug)
        .join(Project, Project.id == Version.project_id)
        .where(Version.id == version_id)
    ).first()
    if row is None:
        raise ValueError(f"Version {version_id} not found")
    version_number, slug = row

    # Mirrors orchestrator._version_spec_rel / _priprava_directive: the Zadanie lives at
    # docs/specs/versions/v<version_number>/customer-requirements.md.
    rel_path = f"docs/specs/versions/v{version_number}/customer-requirements.md"
    abs_path = (_PROJECTS_ROOT / slug / rel_path).resolve()

    # Defense in depth: the resolved path must stay within the project workspace.
    project_root = (_PROJECTS_ROOT / slug).resolve()
    try:
        abs_path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("Resolved Zadanie path escapes the project root") from exc

    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    return rel_path


def update(db: Session, version_id: UUID, data: VersionUpdate) -> Version:
    """Partially update a version.

    Only ``version_number``, ``name``, ``status``, ``description``,
    ``target_date`` and ``release_date`` may be changed — ``id``,
    ``project_id`` and ``created_at`` are immutable and ``updated_at``
    is auto-stamped by the ORM ``onupdate=func.now()`` trigger.

    PATCH semantics: fields that are ``None`` in the payload are left
    untouched. This means the "clear to NULL" transitions (e.g. "remove
    target_date") are not expressible through this service; they are
    rare corrections that belong to admin tooling.

    .. note::

       Directly patching ``status = 'released'`` here *does not* enforce
       the release gate (DESIGN.md §4.0 Rule 5). Callers must use
       :func:`release` for the gated transition; :func:`update` is
       reserved for backfill / correction flows. The DB-level CHECK
       constraint still rejects any value outside
       ``planned | active | released``.

    When ``version_number`` changes the new pair is re-validated
    against the ``UNIQUE(project_id, version_number)`` constraint to
    surface conflicts as a clean :class:`ValueError`.

    Raises:
        ValueError: If the version does not exist, or if the renamed
            ``version_number`` collides with an existing sibling in the
            same project.
    """
    version = get_by_id(db, version_id)

    update_data = data.model_dump(exclude_unset=True)
    allowed_fields = {
        "version_number",
        "name",
        "status",
        "description",
        "target_date",
        "release_date",
    }

    new_version_number = update_data.get("version_number")
    if new_version_number is not None and new_version_number != version.version_number:
        existing = _get_by_project_and_version_number(db, version.project_id, new_version_number)
        if existing is not None and existing.id != version.id:
            raise ValueError(
                f"Version with project_id={version.project_id} and version_number={new_version_number!r} already exists"
            )

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(version, field, value)

    db.flush()
    return version


def release(db: Session, version_id: UUID) -> Version:
    """Release a version — the DESIGN.md §4.0 Rule 5 release gate.

    Fetches every EPIC assigned to the version and checks that they all
    have ``status = 'done'``. If one or more EPICs are still in
    ``planned`` or ``in_progress`` the transition is rejected with a
    :class:`ValueError` whose message contains the list of blocking
    EPIC IDs (the router converts this to HTTP 409 with a structured
    payload — see DESIGN.md §2.6 ``POST /versions/{id}/release``).

    On success the service sets ``status = 'released'`` and
    ``release_date = today`` (local ``date.today()``). Idempotent
    protection: a version already in ``released`` state cannot be
    re-released (``ValueError``); a version still in ``planned`` state
    can be released directly provided the gate passes (no intermediate
    ``active`` transition required — e.g. for hot-patch releases with
    zero EPICs).

    Args:
        db: Active SQLAlchemy session.
        version_id: Identifier of the version to release.

    Returns:
        The released :class:`Version` (status ``released``,
        ``release_date`` set to today) after flush.

    Raises:
        ValueError: If the version does not exist, is already in
            ``released`` state, or has one or more EPICs with
            ``status != 'done'``.
    """
    version = get_by_id(db, version_id)

    if version.status == "released":
        raise ValueError(f"Version {version_id} is already released")

    blocking_stmt = (
        select(Epic.id).where(Epic.version_id == version_id, Epic.status != "done").order_by(Epic.number.asc())
    )
    blocking_ids = [row[0] for row in db.execute(blocking_stmt).all()]
    if blocking_ids:
        blocking_str = ", ".join(str(eid) for eid in blocking_ids)
        raise ValueError(f"Cannot release version {version_id}: blocking EPICs (status != 'done'): [{blocking_str}]")

    version.status = "released"
    version.release_date = date.today()
    # E2 (CR-NS-041): additively realize this version's included backlog items (included → realized +
    # realized_at). Runs AFTER the blocking-epic gate above — purely additive, never affects the release
    # decision. No-op when the project has no backlog / none assigned to this version.
    backlog_service.realize_for_version(db, version_id)
    db.flush()
    return version


def delete(db: Session, version_id: UUID) -> None:
    """Delete a version permanently.

    Safety checks (applied in order):

    1. Version must exist (``ValueError`` → HTTP 404 if not).
    2. Status must not be ``released`` — released versions are immutable
       artefacts of project history (``ValueError`` → HTTP 409).
    3. The version must have no EPICs (``epic_count == 0``) — an EPIC
       constitutes a Task Plan entry; deleting a version with attached
       EPICs would silently orphan planning data.  The DB-level
       ``ON DELETE RESTRICT`` on ``epics.version_id`` provides a
       second-line defence, but the pre-check gives a friendlier error
       message (``ValueError`` → HTTP 409).

    Bugs are not checked separately because a BUG without an EPIC
    attached to the same version is unusual and the DB RESTRICT on
    ``bugs.version_id`` covers that edge.

    Args:
        db: Active SQLAlchemy session.
        version_id: Primary key of the version to delete.

    Raises:
        ValueError: If the version does not exist, is ``released``, or
            still has at least one EPIC.
    """
    # Re-use get_by_id so we get a proper 404 path on missing rows.
    version = get_by_id(db, version_id)

    if version.status == "released":
        raise ValueError(f"Cannot delete released version {version_id}")

    epic_count_stmt = select(func.count(Epic.id)).where(Epic.version_id == version_id)
    epic_count = db.execute(epic_count_stmt).scalar_one()
    if epic_count > 0:
        raise ValueError(f"Cannot delete version {version_id}: it has {epic_count} EPIC(s) (Task Plan not empty)")

    db.delete(version)
    db.flush()


def auto_activate(db: Session, version_id: UUID) -> Version:
    """Auto-transition a ``planned`` version to ``active``.

    Implements DESIGN.md §4.0 Rule 4 — called by the Epic service
    whenever an EPIC transitions into ``in_progress`` (Task 9.4). If
    the referenced version is currently in ``planned`` state it is
    promoted to ``active``; otherwise the call is a safe no-op
    (``active`` / ``released`` are forward states under Rule 3 and must
    not regress).

    Args:
        db: Active SQLAlchemy session.
        version_id: Identifier of the version whose status should be
            auto-advanced.

    Returns:
        The :class:`Version` row (possibly unchanged).

    Raises:
        ValueError: If the version does not exist.
    """
    version = get_by_id(db, version_id)
    if version.status == "planned":
        version.status = "active"
        db.flush()
    return version
