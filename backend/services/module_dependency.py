"""Service layer for :class:`~backend.db.models.projects.ModuleDependency`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.2 ``module_dependencies`` table, D-10 NEX
Horizont module seeding, and
:class:`backend.db.models.projects.ModuleDependency`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``(module_id, depends_on_module_id)`` is the natural key of the
      row — ``UNIQUE(module_id, depends_on_module_id)`` (see
      ``uq_module_dependencies_module_id_depends_on_module_id``). Both
      columns are immutable: a dependency edge is a join row in the
      module DAG that is either created or deleted, never rewritten in
      place (changing either endpoint would produce a different edge).
      The :class:`ModuleDependencyUpdate` schema therefore exposes no
      mutable fields; the service's allow-list formalises that contract
      defensively.
    * :func:`create` rejects self-loops (``module_id ==
      depends_on_module_id``) pre-emptively with a clean
      :class:`ValueError` — a module cannot depend on itself. The DB
      has no CHECK constraint for this (it cannot cheaply express
      graph-level predicates), so the service is the enforcement
      point. Full cycle detection across multi-hop paths is
      Architect / ModuleService territory (DESIGN.md §1.2
      "Application-level cycle detection"); self-loops are the
      trivial one-hop case and are caught here.
    * Unique constraint on ``(module_id, depends_on_module_id)`` is
      enforced both at the DB layer and pre-emptively by
      :func:`create`, so callers receive a clean :class:`ValueError`
      (HTTP 409 at the router layer) instead of a raw
      :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.
    * ``module_dependencies`` has **no** inbound foreign keys — no
      other table references it — so :func:`delete` performs no
      dependency RESTRICT check. Both outbound FKs (``module_id`` and
      ``depends_on_module_id``) use ``ON DELETE CASCADE`` targeting
      ``project_modules.id``, so deleting a module cleans up its
      edges automatically.
    * List filters (``module_id``, ``depends_on_module_id``) match
      the two indexed FK columns and support the two typical graph
      queries: "what does this module depend on" (outgoing edges,
      used by ``ModuleService.start_module()`` DESIGN.md §1.2
      business rule) and "which modules depend on this one"
      (incoming edges, used by the dependency-graph visualisation in
      ``ModuleGraph`` — DESIGN.md §3.2).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.projects import ModuleDependency
from backend.schemas.module_dependency import (
    ModuleDependencyCreate,
    ModuleDependencyUpdate,
)


def list_module_dependencies(
    db: Session,
    *,
    module_id: Optional[UUID] = None,
    depends_on_module_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ModuleDependency]:
    """Return module dependency edges filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    created edges appear first, matching the Module Registry UI
    convention.

    Args:
        db: Active SQLAlchemy session.
        module_id: Optional filter — restrict to edges whose dependent
            endpoint is the given module (the "what does this module
            depend on" query; used by
            ``ModuleService.start_module()`` DESIGN.md §1.2 business
            rule to check that every prerequisite has status ``done``).
        depends_on_module_id: Optional filter — restrict to edges
            whose prerequisite endpoint is the given module (the
            "which modules depend on this one" query; used by the
            dependency-graph visualisation in ``ModuleGraph`` —
            DESIGN.md §3.2).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ModuleDependency` instances.
    """
    stmt = select(ModuleDependency)
    if module_id is not None:
        stmt = stmt.where(ModuleDependency.module_id == module_id)
    if depends_on_module_id is not None:
        stmt = stmt.where(ModuleDependency.depends_on_module_id == depends_on_module_id)
    stmt = stmt.order_by(ModuleDependency.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, dependency_id: UUID) -> ModuleDependency:
    """Return a single module dependency edge by primary key.

    Raises:
        ValueError: If no edge with the supplied ``dependency_id``
            exists. The router converts this to an HTTP 404 response.
    """
    dependency = db.get(ModuleDependency, dependency_id)
    if dependency is None:
        raise ValueError(f"ModuleDependency {dependency_id} not found")
    return dependency


def _get_by_natural_key(
    db: Session,
    module_id: UUID,
    depends_on_module_id: UUID,
) -> Optional[ModuleDependency]:
    """Internal helper — look up an edge by its ``(module_id, depends_on_module_id)`` natural key."""
    stmt = select(ModuleDependency).where(
        ModuleDependency.module_id == module_id,
        ModuleDependency.depends_on_module_id == depends_on_module_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: ModuleDependencyCreate) -> ModuleDependency:
    """Create a new module dependency edge.

    Validates two invariants before insertion so the caller receives a
    clean :class:`ValueError` (HTTP 409 at the router layer) instead of
    a raw :class:`~sqlalchemy.exc.IntegrityError` or a silent cycle:

        1. ``module_id != depends_on_module_id`` — a module cannot
           depend on itself. The DB has no CHECK constraint for this
           predicate; the service is the enforcement point. Full
           multi-hop cycle detection is the caller's responsibility
           (DESIGN.md §1.2).
        2. ``UNIQUE(module_id, depends_on_module_id)`` — the edge does
           not already exist.

    If either ``module_id`` or ``depends_on_module_id`` does not match
    an existing ``project_modules.id``, the DB-level FK rejects the
    flush and the error propagates as-is (routed at the API layer as a
    409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`ModuleDependency` with
        its server-generated ``id``, ``created_at`` and ``updated_at``
        populated.

    Raises:
        ValueError: If the edge is a self-loop, or if an edge with the
            same ``(module_id, depends_on_module_id)`` pair already
            exists.
    """
    if data.module_id == data.depends_on_module_id:
        raise ValueError("ModuleDependency cannot reference the same module on both endpoints (self-loop)")

    if _get_by_natural_key(db, data.module_id, data.depends_on_module_id) is not None:
        raise ValueError(
            f"ModuleDependency for module_id={data.module_id} "
            f"depends_on_module_id={data.depends_on_module_id} already exists"
        )

    dependency = ModuleDependency(
        module_id=data.module_id,
        depends_on_module_id=data.depends_on_module_id,
    )
    db.add(dependency)
    db.flush()
    return dependency


def update(
    db: Session,
    dependency_id: UUID,
    data: ModuleDependencyUpdate,
) -> ModuleDependency:
    """Partially update a module dependency edge.

    :class:`ModuleDependency` has no mutable columns — ``id``,
    ``module_id``, ``depends_on_module_id``, ``created_at`` and
    ``updated_at`` are all immutable. ``module_id`` /
    ``depends_on_module_id`` form the natural key and must not be
    rewritten after the fact (a different pair is a different edge);
    ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    :class:`ModuleDependencyUpdate` therefore exposes no fields; the
    service's empty allow-list formalises that contract defensively.
    This function exists for symmetry with the rest of the CRUD
    surface — it confirms the row exists (raising :class:`ValueError`
    if not) and returns the unmodified instance. Redirecting an edge
    is a create/delete operation, not an in-place edit.

    Raises:
        ValueError: If the edge does not exist.
    """
    dependency = get_by_id(db, dependency_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — :class:`ModuleDependencyUpdate` has no fields
    # so ``update_data`` is always empty. If a future schema change
    # ever adds a field without updating this allow-list, the field
    # will be silently dropped here rather than silently leaking
    # through.
    allowed_fields: set[str] = set()

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(dependency, field, value)

    db.flush()
    return dependency


def delete(db: Session, dependency_id: UUID) -> None:
    """Hard-delete a module dependency edge.

    ``module_dependencies`` has no inbound FKs — no other table
    references it — so no dependency RESTRICT check is required.
    Outbound FKs (``module_id``, ``depends_on_module_id``) both use
    ``ON DELETE CASCADE``, so deleting either parent module cleans up
    the edge automatically; this function is the explicit inverse,
    removing a single edge from the DAG (the "break dependency" flow
    in the module-registry / dependency-graph UI).

    Raises:
        ValueError: If the edge does not exist.
    """
    dependency = get_by_id(db, dependency_id)
    db.delete(dependency)
    db.flush()
