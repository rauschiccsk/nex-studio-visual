"""Service layer for the per-project specs browser (``/api/v1/project-specs``).

Browses the **real filesystem** under ``/opt/projects/<slug>/docs/`` for
any project that physically exists on disk. The router layer applies
RBAC (ri only for the v1 cut) and path-traversal validation before
invoking this service.

Director directive 2026-05-14: Project Specs must reflect what is
actually on disk — all file types (not just ``.md``) and empty
directories that the user has created (e.g. ``import/``, ``export/``).
The previous ``.md``-only filter hid CSV / XLSX inputs and made empty
folders look like they didn't exist.

Single source of truth — these files are produced and edited by the
three agents (Designer / Implementer / Auditor) directly in the
project repo. The UI exposed by this service is read-mostly with a
Director (ri) override for typo fixes on Markdown files (write/edit
still restricted to ``.md`` — non-Markdown editing is out of scope).

Design notes:
- Scans ``/opt/projects/<slug>/docs/`` recursively for files **and**
  for directories that are empty (so the user sees them in the tree).
- Hidden directories (``.git``, ``__pycache__``, ``node_modules``, ...)
  are skipped — mirrors the KB scanner pattern.
- All returned paths are ``<slug>/docs/...`` (relative to
  ``/opt/projects``), which is the format KbTree expects for the
  unified hierarchical view.
- ``read_content`` returns text for whitelisted extensions; binary
  files yield ``is_text=False`` so the frontend can render a fallback
  message instead of garbage.
- ``write_content`` remains ``.md``-only (Director scope: typo fixes on
  spec docs, not editing CSV / config / binary).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.schemas.project_specs import ProjectSpecDoc

PROJECTS_ROOT = Path("/opt/projects")

#: Directories skipped during the recursive scan — same set as
#: :data:`backend.services.knowledge_manager._HIDDEN_DIRS`.
_HIDDEN_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".github",
        ".venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
)

#: Slug regex — same kebab-case rule as ``init.sh`` validation.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$")

#: Extensions the content endpoint returns as text. Other extensions
#: get ``is_text=False`` and an empty content payload (frontend renders
#: a "binary file — cannot display" placeholder).
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".py",
        ".pyi",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".ini",
        ".cfg",
        ".conf",
        ".env",
        ".log",
        ".rst",
        ".tex",
        ".gitignore",
        ".dockerignore",
    }
)


class ProjectSpecsError(ValueError):
    """Raised on invalid slug or path traversal attempt."""


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ProjectSpecsError(f"Invalid slug: {slug!r}")


def list_all_specs() -> list[ProjectSpecDoc]:
    """Return all files **and** empty directories under every
    ``/opt/projects/*/docs/``.

    Top-level entries in :data:`PROJECTS_ROOT` are filtered to those
    that (a) match the slug regex and (b) contain a ``docs/`` subdir.
    Hidden directories are skipped during the recursive scan.

    Order: deterministic — sorted by ``relative_path`` ascending so the
    UI gets stable layout across reloads.
    """
    if not PROJECTS_ROOT.is_dir():
        return []

    out: list[ProjectSpecDoc] = []
    for project_dir in sorted(PROJECTS_ROOT.iterdir()):
        if not project_dir.is_dir():
            continue
        slug = project_dir.name
        if not _SLUG_RE.match(slug):
            continue
        docs_dir = project_dir / "docs"
        if not docs_dir.is_dir():
            continue
        out.extend(_scan_docs(slug, docs_dir))

    out.sort(key=lambda d: d.relative_path)
    return out


def _scan_docs(slug: str, docs_dir: Path) -> list[ProjectSpecDoc]:
    """Recursive scan of ``docs_dir`` for files + empty directories.

    Two-pass walk:
    1. Collect every file (any extension, any depth), skipping any path
       that has a hidden-directory segment.
    2. Collect every directory that turned out to be empty (after the
       hidden-dir skip and the file collection above) — these become
       synthetic ``is_directory=True`` entries so the frontend tree
       builder can render them as visible leaf folders.

    Non-empty directories are intentionally not emitted as entries —
    the tree builder creates them implicitly from the paths of files
    underneath, which keeps the response payload tight.
    """
    out: list[ProjectSpecDoc] = []

    # Pass 1 — files.
    files_under: set[Path] = set()
    for path in docs_dir.rglob("*"):
        if any(part in _HIDDEN_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        files_under.add(path.parent)
        rel_to_root = path.relative_to(PROJECTS_ROOT)
        parent_rel = rel_to_root.parent.as_posix()
        out.append(
            ProjectSpecDoc(
                relative_path=rel_to_root.as_posix(),
                filename=path.name,
                category=parent_rel,
                size_bytes=path.stat().st_size,
                is_directory=False,
            )
        )

    # Pass 2 — empty directories. A directory is "empty" iff no file
    # under it (anywhere down the tree) was emitted in pass 1. This
    # avoids redundant entries for non-empty folders, which the tree
    # builder will create implicitly.
    for path in docs_dir.rglob("*"):
        if any(part in _HIDDEN_DIRS for part in path.parts):
            continue
        if not path.is_dir():
            continue
        # If any emitted file lives under this directory, skip.
        has_descendant_file = any(p == path or _is_under(p, path) for p in files_under)
        if has_descendant_file:
            continue
        rel_to_root = path.relative_to(PROJECTS_ROOT)
        parent_rel = rel_to_root.parent.as_posix()
        out.append(
            ProjectSpecDoc(
                relative_path=rel_to_root.as_posix(),
                filename=path.name,
                category=parent_rel,
                size_bytes=0,
                is_directory=True,
            )
        )

    return out


def _is_under(child: Path, parent: Path) -> bool:
    """True iff ``child`` is at or under ``parent`` in the filesystem tree."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def read_content(slug: str, path_within_project: str) -> tuple[str, bool]:
    """Read a single file under ``/opt/projects/<slug>/docs/``.

    Returns ``(content, is_text)``. For text files (whitelist of
    extensions), ``content`` is the decoded UTF-8 string and ``is_text``
    is True. For binary files, ``content`` is an empty string and
    ``is_text`` is False — the frontend renders a "binary, cannot
    display" placeholder rather than garbled output.

    ``path_within_project`` is the part after the slug, e.g.
    ``docs/specs/customer-requirements.md``. Must start with ``docs/``
    and contain no ``..`` segments.

    Raises:
        ProjectSpecsError: invalid slug, path traversal attempt, path
            outside ``docs/``, or file not found.
    """
    abs_path = _resolve_read_path(slug, path_within_project)
    if not abs_path.is_file():
        raise ProjectSpecsError(
            f"File not found: {slug}/{path_within_project}",
        )

    if abs_path.suffix.lower() not in _TEXT_EXTENSIONS:
        # Binary or unknown — frontend renders a placeholder.
        return "", False

    try:
        return abs_path.read_text(encoding="utf-8"), True
    except UnicodeDecodeError:
        # Whitelisted extension but undecodable bytes (e.g. legacy
        # cp1250 file saved as .txt) — treat as binary for safety.
        return "", False


def _resolve_read_path(slug: str, path_within_project: str) -> Path:
    """Validate + resolve for reads. Accepts any file extension."""
    return _resolve_doc_path(slug, path_within_project, require_md=False)


def _resolve_doc_path(slug: str, path_within_project: str, *, require_md: bool) -> Path:
    """Validate + resolve. Common helper for read + write paths."""
    _validate_slug(slug)

    if ".." in path_within_project.split("/"):
        raise ProjectSpecsError("Path traversal not allowed")
    if not path_within_project.startswith("docs/"):
        raise ProjectSpecsError("Path must be inside docs/")
    if require_md and not path_within_project.endswith(".md"):
        raise ProjectSpecsError("Edit endpoint accepts only .md files")

    project_root = PROJECTS_ROOT / slug
    abs_path = (project_root / path_within_project).resolve()

    # Defense in depth — even though we banned ``..`` above, ensure the
    # resolved path stays within the project root.
    try:
        abs_path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ProjectSpecsError("Resolved path escapes project root") from exc

    return abs_path
