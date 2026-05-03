"""Filesystem writer for per-project live documents in the Knowledge Base.

Backs :mod:`backend.services.live_documents` (forthcoming) with the narrow
set of I/O operations needed to maintain the three live project
documents — ``STATUS.md``, ``ARCHITECT.md`` and ``HISTORY.md`` — under
``{knowledge_base_path}/projects/{slug}/``.

Design notes (per ``docs/architect/live-docs-port.md``):

    * **Deliberately narrow surface.** Only ``save`` (overwrite),
      ``read``, ``append`` (with dedup guard) and ``exists`` are
      exposed. No listing, no deletion, no directory traversal — live
      documents are append-mostly and the writer must not become a
      general KB editor.
    * **Strict allow-list.** ``project_slug`` must match the same
      ``[a-z0-9][a-z0-9-]*`` pattern the Project model enforces on
      ``slug`` creation, and ``filename`` must be one of
      :data:`ALLOWED_FILENAMES`. This stops callers from accidentally
      (or maliciously) landing content in unrelated parts of the KB
      tree — the backend container mounts ``/home/icc/knowledge`` rw
      to allow these writes, and the writer is the single choke point
      that keeps that privilege scoped.
    * **Atomic writes.** ``save`` writes to a sibling ``*.tmp`` file
      and then ``os.replace``'s it onto the target — crash-safe against
      half-written files during concurrent updates. ``append`` reads
      existing content, concatenates the entry, and goes through the
      same atomic path.
    * **Dedup on append.** The first line of the incoming ``entry`` is
      treated as a unique marker; if it already appears anywhere in
      the existing file, the append is skipped. Mirrors the NEX
      Command behaviour (``backend/services/live_documents.py``
      ``_append_to_kb``) so replaying a task completion stays
      idempotent.
    * **No DB access.** The writer is deliberately DB-agnostic —
      caller-supplied slug is assumed valid (the live-document
      service validates it via the ORM before calling). Keeps this
      layer trivially testable with ``tmp_path``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Filenames the writer is allowed to touch. Extended only via a
#: documented change in ``docs/architect/live-docs-port.md``.
ALLOWED_FILENAMES: frozenset[str] = frozenset(
    {
        "STATUS.md",
        "ARCHITECT.md",
        "HISTORY.md",
    }
)


class KnowledgeBaseWriter:
    """Narrow, allow-list-guarded writer for per-project live docs.

    Instantiate with the absolute path to the Knowledge Base root
    (``Settings.knowledge_base_path`` in production, a ``tmp_path``
    fixture in tests). All writes land under
    ``{base_path}/projects/{project_slug}/``.
    """

    def __init__(self, base_path: Path | str) -> None:
        self._base_path = Path(base_path).resolve()

    # ── public API ────────────────────────────────────────────────────

    def save(self, project_slug: str, filename: str, content: str) -> Path:
        """Atomically write ``content`` to ``projects/{slug}/{filename}``.

        Overwrites any existing file. Parent directory is created on
        demand. Returns the absolute path written.
        """
        target = self._resolve(project_slug, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, content)
        return target

    def read(self, project_slug: str, filename: str) -> str:
        """Return the current content of ``projects/{slug}/{filename}``.

        Raises :class:`FileNotFoundError` if the file does not exist.
        """
        target = self._resolve(project_slug, filename)
        return target.read_text(encoding="utf-8")

    def append(
        self,
        project_slug: str,
        filename: str,
        entry: str,
        *,
        header_if_new: str = "",
    ) -> Path:
        """Append ``entry`` to ``projects/{slug}/{filename}``.

        If the file does not yet exist, it is created with
        ``header_if_new`` as its opening content (typically a markdown
        ``# Title`` line). A blank line separates the existing content
        from the appended entry.

        **Dedup**: if the first non-empty line of ``entry`` already
        appears verbatim anywhere in the existing file, the append is
        skipped — the target is returned unchanged. This keeps replays
        of the same task completion idempotent.
        """
        target = self._resolve(project_slug, filename)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            existing = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = header_if_new

        marker = _first_nonempty_line(entry)
        if marker and marker in existing:
            return target

        joiner = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        new_content = existing + joiner + entry
        if not new_content.endswith("\n"):
            new_content += "\n"

        self._atomic_write(target, new_content)
        return target

    def exists(self, project_slug: str, filename: str) -> bool:
        """Return whether ``projects/{slug}/{filename}`` exists on disk."""
        return self._resolve(project_slug, filename).is_file()

    def delete_project(self, project_slug: str) -> bool:
        """Remove the project's KB folder (``projects/{slug}/``) entirely.

        Intended for the DELETE /projects/{id} flow — after the DB row
        is gone the live documents have no owner, and leaving them in
        place was tracked as the open item surfaced in the Krok 9b
        audit. Returns ``True`` when the folder existed and was
        removed, ``False`` when there was nothing to remove.

        Guards against path traversal via the same slug regex that
        protects writes, then physically verifies the resolved path
        is inside ``{base}/projects/`` before ``rmtree``-ing.
        """
        if not _SLUG_RE.match(project_slug):
            raise ValueError(f"Invalid project slug: {project_slug!r}. Expected lowercase alphanumeric with hyphens.")

        projects_root = (self._base_path / "projects").resolve()
        target = (projects_root / project_slug).resolve()

        try:
            target.relative_to(projects_root)
        except ValueError as exc:
            raise ValueError(f"Resolved path escapes Knowledge Base projects root: {target}") from exc

        if not target.is_dir():
            return False

        import shutil

        shutil.rmtree(target)
        return True

    # ── internals ─────────────────────────────────────────────────────

    def _resolve(self, project_slug: str, filename: str) -> Path:
        """Validate inputs and resolve the absolute target path.

        Guards:
            * ``project_slug`` matches :data:`_SLUG_RE`.
            * ``filename`` is in :data:`ALLOWED_FILENAMES`.
            * Resolved path is a descendant of ``{base_path}/projects``.
        """
        if not _SLUG_RE.match(project_slug):
            raise ValueError(f"Invalid project slug: {project_slug!r}. Expected lowercase alphanumeric with hyphens.")
        if filename not in ALLOWED_FILENAMES:
            raise ValueError(f"Invalid filename: {filename!r}. Allowed: {sorted(ALLOWED_FILENAMES)}.")

        projects_root = (self._base_path / "projects").resolve()
        target = (projects_root / project_slug / filename).resolve()

        # Belt and suspenders — slug regex already excludes traversal,
        # but the physical check stays honest against symlink games.
        try:
            target.relative_to(projects_root)
        except ValueError as exc:
            raise ValueError(f"Resolved path escapes Knowledge Base projects root: {target}") from exc

        return target

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        """Write ``content`` to ``target`` via tmp-file + ``os.replace``."""
        tmp = target.with_name(target.name + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, target)
        finally:
            # If os.replace succeeded, tmp is gone; if it failed, clean up.
            if tmp.exists():
                tmp.unlink()


def _first_nonempty_line(text: str) -> str:
    """Return the first non-blank line of ``text``, stripped. Empty string if none."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
