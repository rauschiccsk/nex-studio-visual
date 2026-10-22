"""Pydantic schemas for ``/api/v1/project-specs/*`` endpoints.

Reuse the same ``KnowledgeDoc``-shaped tree node so the frontend's
``<KbTree />`` component (built for the KB browser) can consume the
list response without adaptation. The only difference: ``relative_path``
is prefixed with ``<slug>/docs/...`` instead of being KB-root-relative.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectSpecDoc(BaseModel):
    """A single filesystem entry under ``/opt/projects/<slug>/docs/``.

    Fields mirror :class:`KnowledgeDoc` in the frontend so the existing
    KbTree builder works without changes. Director directive 2026-05-14:
    Project Specs must show the **real filesystem** — all files, not just
    ``.md`` — and empty directories that the user has created (e.g. an
    empty ``import/`` folder still appears in the tree).

    ``is_directory=True`` marks a synthetic entry for an empty directory
    (no descendants yet); ``size_bytes`` is 0 for such entries.
    Non-empty directories are implicit — the frontend tree builder
    creates them when files underneath are present.
    """

    relative_path: str = Field(
        ...,
        description=(
            "Path relative to ``/opt/projects/``, e.g. "
            "``nex-inbox/docs/specs/customer-requirements.md`` for a file "
            "or ``nex-inbox/docs/import`` for an empty directory. "
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
    is_directory: bool = Field(
        default=False,
        description=(
            "True for synthetic entries representing empty directories. "
            "Frontend tree builder renders these as leaf folders."
        ),
    )


class ProjectSpecListResponse(BaseModel):
    """Response for ``GET /api/v1/project-specs/list``."""

    documents: list[ProjectSpecDoc]
    count: int


class ProjectSpecContent(BaseModel):
    """Response for ``GET /api/v1/project-specs/content``.

    ``is_text=False`` indicates a binary file that the backend refused to
    return as text — frontend should fall back to a "cannot display"
    message instead of trying to render ``content`` (which will be
    empty in that case). The endpoint never returns raw bytes; binary
    download is out of scope for v1 (Director can SSH for those).
    """

    relative_path: str
    content: str
    is_text: bool = Field(
        default=True,
        description="False if the file is binary and cannot be rendered as text.",
    )
