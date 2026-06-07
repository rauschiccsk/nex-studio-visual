"""Service layer for :class:`~backend.db.models.projects.Project`.

Provides the synchronous CRUD surface used by API routers. All methods accept
``db: Session`` as the first argument and only ever call ``session.flush()`` —
transaction commit is the router's responsibility. Errors are signalled via
``ValueError`` so the router can translate them to the appropriate HTTP
status code.

Design notes (per DESIGN.md §1.3 / §2.2 and :mod:`backend.db.models.projects`):
    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer.
    * ``name`` and ``slug`` are both ``UNIQUE`` — :func:`create` and
      :func:`update` validate these constraints before :meth:`Session.flush`
      so the router receives a clean :class:`ValueError` rather than a raw
      :class:`~sqlalchemy.exc.IntegrityError`.
    * ``category`` is constrained by a CHECK (``singlemodule`` |
      ``multimodule``) and is immutable after creation — it drives the
      module-registry seeding at creation time (see D-10) and cannot be
      swapped later. The Pydantic ``ProjectCategory`` literal mirrors the
      DB constraint.
    * ``status`` is constrained by a CHECK (``active`` | ``archived`` |
      ``paused``). The Pydantic ``ProjectStatus`` literal mirrors the DB
      constraint, so the service does not need to revalidate it — if an
      invalid value ever reaches the service (e.g. a bypassed schema) the
      DB CHECK will reject it on flush.
    * ``slug`` is immutable via :class:`ProjectUpdate` because DESIGN.md
      §2.2 specifies slugs are auto-generated from ``name`` at creation
      and stable thereafter (URLs, filesystem paths, KB locations all
      depend on slug).
    * ``created_by`` is an audit column — immutable after creation.
    * Every inbound FK to ``projects.id`` uses ``ON DELETE CASCADE``
      (``project_modules``, ``raw_specifications``,
      ``professional_specifications``, ``design_documents``, ``kb_documents``,
      ``architect_sessions``, ``epics``, ``bugs``, ``delegations``,
      ``report_configs``). No RESTRICT
      dependency checks are required — deleting a project cleanly
      removes every dependent row via DB-level cascade.
    * List filters (``status``, ``category``, ``created_by``) support the
      dashboard / settings-page project list UI — "show all active
      multimodule projects", "show all projects owned by a user", etc.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.schemas.project import (
    ProjectCategory,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
)
from backend.services import system_setting as system_setting_service


def list_projects(
    db: Session,
    *,
    status: Optional[ProjectStatus] = None,
    category: Optional[ProjectCategory] = None,
    created_by: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Project]:
    """Return projects filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently created
    projects appear first, matching the dashboard project-list convention.

    Args:
        db: Active SQLAlchemy session.
        status: Optional lifecycle-status filter (``active`` | ``archived``
            | ``paused``).
        category: Optional category filter (``singlemodule`` |
            ``multimodule``).
        created_by: Optional filter restricting results to projects
            created by a specific user.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`Project` instances.
    """
    stmt = select(Project)
    if status is not None:
        stmt = stmt.where(Project.status == status)
    if category is not None:
        stmt = stmt.where(Project.category == category)
    if created_by is not None:
        stmt = stmt.where(Project.created_by == created_by)
    stmt = stmt.order_by(Project.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def count_projects(
    db: Session,
    *,
    status: Optional[ProjectStatus] = None,
    category: Optional[ProjectCategory] = None,
    created_by: Optional[UUID] = None,
) -> int:
    """Return the total number of projects matching the given filters.

    Mirrors the ``status`` / ``category`` / ``created_by`` filters of
    :func:`list_projects` so a paginated response can report the unfiltered
    total alongside the current page of items.

    Args:
        db: Active SQLAlchemy session.
        status: Optional lifecycle-status filter (``active`` | ``archived``
            | ``paused``).
        category: Optional category filter (``singlemodule`` |
            ``multimodule``).
        created_by: Optional filter restricting results to projects
            created by a specific user.

    Returns:
        Total number of rows matching the filters.
    """
    stmt = select(func.count()).select_from(Project)
    if status is not None:
        stmt = stmt.where(Project.status == status)
    if category is not None:
        stmt = stmt.where(Project.category == category)
    if created_by is not None:
        stmt = stmt.where(Project.created_by == created_by)
    return int(db.execute(stmt).scalar_one())


def get_by_id(db: Session, project_id: UUID) -> Project:
    """Return a single project by primary key.

    Raises:
        ValueError: If no project with the supplied ``project_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")
    return project


def _get_by_name(db: Session, name: str) -> Optional[Project]:
    """Internal helper — look up a project by unique name."""
    stmt = select(Project).where(Project.name == name)
    return db.execute(stmt).scalar_one_or_none()


def _get_by_slug(db: Session, slug: str) -> Optional[Project]:
    """Internal helper — look up a project by unique slug."""
    stmt = select(Project).where(Project.slug == slug)
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: ProjectCreate) -> Project:
    """Create a new project.

    Validates both unique constraints (``name``, ``slug``) before
    insertion so the caller receives a clean :class:`ValueError` (HTTP 409
    at the router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`Project` with its server-
        generated ``id``, ``created_at`` and ``updated_at`` populated.

    Raises:
        ValueError: If another project already uses the same ``name`` or
            ``slug``.
    """
    if _get_by_name(db, data.name) is not None:
        raise ValueError(f"Project with name {data.name!r} already exists")
    if _get_by_slug(db, data.slug) is not None:
        raise ValueError(f"Project with slug {data.slug!r} already exists")

    # Convention-based defaults for filesystem paths — only applied when the
    # caller did not supply an explicit value. The templates live in
    # ``system_settings`` (keys ``default_source_path_template`` +
    # ``default_kb_path_template``) so operators running NEX Studio in
    # a different layout can adjust the defaults via the Settings UI
    # without a code change. ``{slug}`` is substituted with the project
    # slug; any other placeholders are left intact.
    source_tmpl = system_setting_service.get_str(db, "default_source_path_template")
    kb_tmpl = system_setting_service.get_str(db, "default_kb_path_template")
    source_path = data.source_path or source_tmpl.format(slug=data.slug)
    kb_path = data.kb_path or kb_tmpl.format(slug=data.slug)

    project = Project(
        name=data.name,
        slug=data.slug,
        category=data.category,
        description=data.description,
        status=data.status,
        backend_port=data.backend_port,
        frontend_port=data.frontend_port,
        db_port=data.db_port,
        repo_url=data.repo_url,
        source_path=source_path,
        kb_path=kb_path,
        guardian_enabled=data.guardian_enabled,
        created_by=data.created_by,
        owner_id=data.owner_id,
    )
    db.add(project)
    db.flush()
    return project


def update(db: Session, project_id: UUID, data: ProjectUpdate) -> Project:
    """Partially update a project.

    Only the fields listed in DESIGN.md §2.2 (``name``, ``description``,
    ``status``, ``backend_port``, ``frontend_port``, ``db_port``,
    ``repo_url``, ``source_path``, ``kb_path``, ``guardian_enabled``) may
    be changed. ``id``, ``slug``, ``category``, ``created_by`` and
    ``created_at`` are immutable; ``updated_at`` is refreshed automatically
    by the ORM ``onupdate=func.now()`` trigger. Fields that are ``None``
    in the payload are treated as "leave unchanged" to support PATCH
    semantics.

    Uniqueness of ``name`` is re-validated when the field is changed so
    the caller receives a clean :class:`ValueError` rather than a DB-level
    integrity error.

    Raises:
        ValueError: If the project does not exist, or if a new ``name``
            collides with another project.
    """
    project = get_by_id(db, project_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields, but
    # silently dropping any that slip through keeps the service honest.
    allowed_fields = {
        "name",
        "description",
        "status",
        "backend_port",
        "frontend_port",
        "db_port",
        "repo_url",
        "source_path",
        "kb_path",
        "guardian_enabled",
    }

    # Uniqueness check only for an actually-changing ``name``.
    new_name = update_data.get("name")
    if new_name is not None and new_name != project.name:
        existing = _get_by_name(db, new_name)
        if existing is not None and existing.id != project.id:
            raise ValueError(f"Project with name {new_name!r} already exists")

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(project, field, value)

    db.flush()
    return project


def delete(db: Session, project_id: UUID) -> None:
    """Hard-delete a project.

    Every inbound foreign key to ``projects.id`` uses
    ``ON DELETE CASCADE``, so dependent rows (modules, specifications,
    design documents, KB docs, architect sessions, epics, bugs,
    delegations, migration tables, report configs) are removed
    automatically at the DB level. No RESTRICT dependency check is
    required. Archiving is the preferred soft-disable path — callers
    should prefer :func:`update` with ``status='archived'`` and reserve
    :func:`delete` for test fixtures / admin tooling.

    Raises:
        ValueError: If the project does not exist.
    """
    project = get_by_id(db, project_id)
    db.delete(project)
    db.flush()
