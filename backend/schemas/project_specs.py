"""Pydantic schemas for ``/api/v1/project-specs/*`` endpoints.

Reuse the same ``KnowledgeDoc``-shaped tree node so the frontend's
``<KbTree />`` component (built for the KB browser) can consume the
list response without adaptation. The only difference: ``relative_path``
is prefixed with ``<slug>/docs/...`` instead of being KB-root-relative.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectSpecDoc(BaseModel):
    """A single ``.md`` file under ``/opt/projects/<slug>/docs/``.

    Fields mirror :class:`KnowledgeDoc` in the frontend so the existing
    KbTree builder works without changes.
    """

    relative_path: str = Field(
        ...,
        description=(
            "Path relative to ``/opt/projects/``, e.g. "
            "``nex-inbox/docs/specs/customer-requirements.md``. "
            "Top-level segment is the project slug — this is what KbTree "
            "renders as the root folder."
        ),
    )
    filename: str
    category: str = Field(
        ...,
        description=(
            "Parent folder path within the project, e.g. ``nex-inbox/docs/specs`` or ``nex-inbox/docs/audits/v0.1.0``."
        ),
    )
    size_bytes: int


class ProjectSpecListResponse(BaseModel):
    """Response for ``GET /api/v1/project-specs/list``."""

    documents: list[ProjectSpecDoc]
    count: int


class ProjectSpecContent(BaseModel):
    """Response for ``GET /api/v1/project-specs/content``."""

    relative_path: str
    content: str


class ProjectSpecUpdate(BaseModel):
    """Request body for ``PUT /api/v1/project-specs/content``."""

    content: str
