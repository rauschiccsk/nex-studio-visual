"""Compatibility re-export for the project service.

The canonical implementation lives in :mod:`backend.services.project`.
This module re-exports the public API so that
``from backend.services.project_service import ...`` works as an alias.

Member-related methods (``add_member``, ``remove_member``,
``get_project_members``, ``get_projects_for_user``) were removed as part
of the ProjectMember model elimination — project membership is no longer
tracked. The remaining public surface is pure project CRUD:

* :func:`list_projects` — returns **all** projects (no member filtering),
  with optional status / category / created_by filters and pagination.
* :func:`count_projects` — total count matching the same filters.
* :func:`get_by_id` — single project by primary key.
* :func:`create` — create a new project (validates unique name + slug).
* :func:`update` — partial update of mutable fields.
* :func:`delete` — hard-delete with DB-level CASCADE.
"""

from backend.services.project import (  # noqa: F401
    count_projects,
    create,
    delete,
    get_by_id,
    list_projects,
    update,
)

__all__ = [
    "count_projects",
    "create",
    "delete",
    "get_by_id",
    "list_projects",
    "update",
]
