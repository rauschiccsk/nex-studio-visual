"""Service layer for :class:`~backend.db.models.projects.ProjectModule`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.5 ProjectModule, §2.2 project_modules
table, D-04 per-module DESIGN.md and D-10 NEX Horizont module seeding,
and :mod:`backend.db.models.projects.ProjectModule`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` is an immutable foreign key — a module belongs to
      exactly one project for its lifetime and is deleted rather than
      reassigned. :class:`ProjectModuleUpdate` deliberately omits it and
      the service's ``allowed_fields`` allow-list enforces that contract
      defensively.
    * ``code`` is unique *per project* — ``UNIQUE(project_id, code)``
      (``uq_project_modules_project_id_code``). The same short code
      (e.g. ``'PAB'``) may therefore exist in several projects. Both
      :func:`create` and :func:`update` validate this constraint
      pre-emptively so the caller receives a clean :class:`ValueError`
      (HTTP 409 at the router layer) instead of a raw
      :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.
    * ``status`` is constrained by the ``ck_project_modules_status`` DB
      CHECK (``planned | in_design | in_development | done``). The
      Pydantic :data:`~backend.schemas.project_module.ProjectModuleStatus`
      literal mirrors the DB constraint, so the service does not
      revalidate — if an invalid value ever reaches the service (e.g. a
      bypassed schema) the DB CHECK rejects it on flush.
    * Inbound foreign keys referencing ``project_modules.id`` all use
      either ``ON DELETE CASCADE`` (``module_dependencies.module_id``
      and ``module_dependencies.depends_on_module_id``) or ``ON DELETE
      SET NULL`` (``raw_specifications``, ``professional_specifications``,
      ``kb_documents``, ``tasks``, ``architect_sessions``). No inbound
      FK uses ``RESTRICT``, so :func:`delete` performs no dependency
      check — the DB-level cascade / null-out handles dependent rows
      automatically.
    * List filters (``project_id``, ``status``, ``category``) support
      the Module Registry UI (DESIGN.md §3.1 — ``ModuleRegistryPage``)
      and the dependency-graph visualisation (``ModuleGraph``) — "show
      every module in this project", "show every module in design",
      "show every module in a given category". ``project_id`` matches
      the indexed ``ix_project_modules_project_id`` column.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.projects import ProjectModule
from backend.schemas.project_module import (
    ProjectModuleCreate,
    ProjectModuleStatus,
    ProjectModuleUpdate,
)


def list_project_modules(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    status: Optional[ProjectModuleStatus] = None,
    category: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ProjectModule]:
    """Return project modules filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    created modules appear first, matching the Module Registry UI
    convention (latest modules on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to modules
            belonging to a specific project (the core Module Registry
            query, DESIGN.md §3.1 ``ModuleRegistryPage``).
        status: Optional lifecycle-status filter (``planned`` |
            ``in_design`` | ``in_development`` | ``done``).
        category: Optional category filter (e.g. ``'Katalógy'``,
            ``'Sklad'``, ``'Nákup'``) for grouped display in the
            module graph.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ProjectModule` instances.
    """
    stmt = select(ProjectModule)
    if project_id is not None:
        stmt = stmt.where(ProjectModule.project_id == project_id)
    if status is not None:
        stmt = stmt.where(ProjectModule.status == status)
    if category is not None:
        stmt = stmt.where(ProjectModule.category == category)
    stmt = stmt.order_by(ProjectModule.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, module_id: UUID) -> ProjectModule:
    """Return a single project module by primary key.

    Raises:
        ValueError: If no module with the supplied ``module_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    module = db.get(ProjectModule, module_id)
    if module is None:
        raise ValueError(f"ProjectModule {module_id} not found")
    return module


def _get_by_project_code(
    db: Session,
    project_id: UUID,
    code: str,
) -> Optional[ProjectModule]:
    """Internal helper — look up a module by its ``(project_id, code)`` natural key."""
    stmt = select(ProjectModule).where(
        ProjectModule.project_id == project_id,
        ProjectModule.code == code,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: ProjectModuleCreate) -> ProjectModule:
    """Create a new project module.

    ``status`` defaults to the value set by the Pydantic schema / DB
    ``server_default`` when omitted (``planned``), matching the model
    declaration.

    Validates the ``UNIQUE(project_id, code)`` constraint before
    insertion so the caller receives a clean :class:`ValueError` (HTTP
    409 at the router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``. If
    the supplied ``project_id`` does not match an existing project, the
    DB-level FK rejects the flush and the error propagates as-is
    (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`ProjectModule` with its
        server-generated ``id``, ``created_at`` and ``updated_at``
        populated.

    Raises:
        ValueError: If a module with the same ``code`` already exists
            within the target ``project_id``.
    """
    if _get_by_project_code(db, data.project_id, data.code) is not None:
        raise ValueError(f"ProjectModule with code {data.code!r} already exists in project {data.project_id}")

    module = ProjectModule(
        project_id=data.project_id,
        code=data.code,
        name=data.name,
        category=data.category,
        status=data.status,
        design_doc_path=data.design_doc_path,
    )
    db.add(module)
    db.flush()
    return module


def update(
    db: Session,
    module_id: UUID,
    data: ProjectModuleUpdate,
) -> ProjectModule:
    """Partially update a project module.

    Only ``code``, ``name``, ``category``, ``status`` and
    ``design_doc_path`` may be changed. ``id``, ``project_id``,
    ``created_at`` and ``updated_at`` are immutable — a module belongs
    to exactly one project for its lifetime (modules are deleted
    rather than reassigned) and ``updated_at`` is auto-stamped by the
    ORM on flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics.

    Uniqueness of ``(project_id, code)`` is re-validated when ``code``
    is changed so the caller receives a clean :class:`ValueError`
    rather than a DB-level integrity error.

    Raises:
        ValueError: If the module does not exist, or if the new
            ``code`` collides with another module in the same project.
    """
    module = get_by_id(db, module_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "code",
        "name",
        "category",
        "status",
        "design_doc_path",
    }

    # Uniqueness check only for an actually-changing ``code``.
    new_code = update_data.get("code")
    if new_code is not None and new_code != module.code:
        existing = _get_by_project_code(db, module.project_id, new_code)
        if existing is not None and existing.id != module.id:
            raise ValueError(f"ProjectModule with code {new_code!r} already exists in project {module.project_id}")

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(module, field, value)

    db.flush()
    return module


def delete(db: Session, module_id: UUID) -> None:
    """Hard-delete a project module.

    Inbound foreign keys to ``project_modules.id`` use either
    ``ON DELETE CASCADE`` (``module_dependencies`` edges) or
    ``ON DELETE SET NULL`` (``raw_specifications``,
    ``professional_specifications``, ``kb_documents``, ``tasks``,
    ``architect_sessions``). No inbound FK uses ``RESTRICT``, so no
    dependency check is required — dependent rows are either removed
    or have their module reference nulled out automatically at the DB
    level.

    Raises:
        ValueError: If the module does not exist.
    """
    module = get_by_id(db, module_id)
    db.delete(module)
    db.flush()
