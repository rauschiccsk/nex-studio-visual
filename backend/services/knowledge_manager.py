"""Knowledge Manager — CRUD operations on KB filesystem.

Ported 1:1 from NEX Command (`backend/rag/writer.py`) per Director
mandate 2026-05-07: NEX Studio musí mať identický KB system ako
NEX Command. M1 milestone of Feature Parity Audit
(/home/icc/knowledge/projects/nex-studio/FEATURE_PARITY_AUDIT.md).

Differences from NEX Command source:

* Imports use ``settings.knowledge_base_path`` (NEX Studio Pydantic
  Settings) instead of ``KNOWLEDGE_BASE_PATH`` env constant.
* DB-coupled helpers (``_is_valid_project_slug``, ``_get_all_project_categories``)
  are not ported here — NEX Studio Knowledge endpoint does not need
  them; project listing is independent.
* RBAC helpers (``BLOCKED_CATEGORIES`` for credentials block) are
  KEPT but **credentials are out of KB** in NEX Studio (own store
  ``/opt/data/nex-studio/credentials/`` since session 002, 2026-05-04).
  The constant is preserved for API contract compatibility — the
  block triggers only if a credentials dir reappears under KB root.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from backend.config.settings import settings
from backend.services import kb_owner

logger = logging.getLogger(__name__)


# Static categories — preserved from NEX Command for API contract.
# Dynamic scan of ``base_path`` returns the actual on-disk set; this
# list is reference only (UI may use it for ordering / labels).
STATIC_CATEGORIES = [
    "icc",
    "shuhari",
    "infrastructure",
    "customers/andros",
    "customers/mager",
    "templates",
    "service-manuals",
    "user-manuals",
]

BLOCKED_CATEGORIES = ["credentials"]

# Directories to hide from KB UI at any nesting level.
_HIDDEN_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        ".tox",
        ".nox",
        ".eggs",
        ".idea",
        ".vscode",
    }
)

_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*\.md$")


class KnowledgeManager:
    """CRUD operations on knowledge base markdown files (filesystem-based)."""

    def __init__(self, base_path: Optional[str] = None):
        self.base_path = Path(base_path or settings.knowledge_base_path)

    # --- validation helpers ---

    def _validate_category(self, category: str, *, allow_create: bool = False) -> None:
        """Raise ValueError if category is blocked or does not exist on disk.

        Any directory under the knowledge base path is valid as long as
        it is not in BLOCKED_CATEGORIES. When ``allow_create`` is True,
        the directory does not need to exist yet (used by save).
        """
        normalized = category.strip("/").lower()
        for blocked in BLOCKED_CATEGORIES:
            if normalized == blocked or normalized.startswith(f"{blocked}/"):
                raise ValueError(f"Category '{category}' is not accessible")
        if allow_create:
            return
        cat_path = self.base_path / normalized
        if cat_path.is_dir():
            return
        raise ValueError(f"Invalid category: '{category}'. Directory does not exist in the knowledge base")

    def _validate_filename(self, filename: str) -> None:
        """Raise ValueError for unsafe filenames."""
        if not _SAFE_FILENAME_RE.match(filename):
            raise ValueError(
                f"Invalid filename: '{filename}'. Use alphanumeric, hyphens, underscores, dots. Must end with .md"
            )

    def _safe_resolve(self, relative_path: str) -> Path:
        """Resolve relative_path under base_path, reject traversal."""
        normalized = relative_path.replace("\\", "/")
        if ".." in normalized.split("/"):
            raise ValueError("Path traversal is not allowed")

        resolved = (self.base_path / normalized).resolve()
        base_resolved = self.base_path.resolve()

        if not str(resolved).startswith(str(base_resolved)):
            raise ValueError("Path traversal is not allowed")

        rel_parts = normalized.strip("/").lower().split("/")
        if rel_parts and rel_parts[0] in BLOCKED_CATEGORIES:
            raise ValueError(f"Category '{rel_parts[0]}' is not accessible")

        return resolved

    # --- CRUD ---

    def save_document(self, category: str, filename: str, content: str) -> str:
        """Save markdown file to disk. Returns relative path."""
        self._validate_category(category, allow_create=True)
        self._validate_filename(filename)

        file_path = self.base_path / category / filename
        # DEV-111: the new document and every new folder belong to the owner of the Znalostná báza, not to root.
        kb_owner.write_text(self.base_path, file_path, content)

        relative = f"{category}/{filename}"
        logger.info(f"Saved knowledge document: {relative}")
        return relative

    def read_document(self, relative_path: str) -> str:
        """Read markdown content from disk."""
        resolved = self._safe_resolve(relative_path)
        if not resolved.exists():
            raise FileNotFoundError(f"Document not found: {relative_path}")
        return resolved.read_text(encoding="utf-8")

    def update_document(self, relative_path: str, content: str) -> str:
        """Overwrite existing markdown file. Returns relative path."""
        resolved = self._safe_resolve(relative_path)
        if not resolved.exists():
            raise FileNotFoundError(f"Document not found: {relative_path}")

        kb_owner.write_text(self.base_path, resolved, content)
        logger.info(f"Updated knowledge document: {relative_path}")
        return relative_path

    def delete_document(self, relative_path: str) -> bool:
        """Delete markdown file from disk."""
        resolved = self._safe_resolve(relative_path)
        if not resolved.exists():
            return False

        resolved.unlink()
        logger.info(f"Deleted knowledge document: {relative_path}")
        return True

    def list_documents(self, category: Optional[str] = None) -> list[dict]:
        """List markdown files, optionally filtered by category."""
        results: list[dict] = []

        if category:
            normalized = category.strip("/").lower()
            for blocked in BLOCKED_CATEGORIES:
                if normalized == blocked or normalized.startswith(f"{blocked}/"):
                    return []
            search_path = self.base_path / category
            if not search_path.is_dir():
                return []
            md_files = search_path.rglob("*.md")
        else:
            md_files = self.base_path.rglob("*.md")

        base_resolved = self.base_path.resolve()

        for fp in sorted(md_files):
            # Skip files under hidden directories at any nesting level.
            if any(part.lower() in _HIDDEN_DIRS for part in fp.parts):
                continue
            resolved = fp.resolve()
            relative = str(resolved.relative_to(base_resolved)).replace("\\", "/")

            top_dir = relative.split("/")[0].lower()
            if top_dir in BLOCKED_CATEGORIES:
                continue

            results.append(
                {
                    "relative_path": relative,
                    "filename": fp.name,
                    "category": self._extract_category(relative),
                    "size_bytes": fp.stat().st_size,
                }
            )

        return results

    def get_categories(self) -> list[str]:
        """Return list of existing category directories from disk.

        Dynamically scans the knowledge base directory. All top-level
        directories are included except BLOCKED_CATEGORIES. Directories
        that contain subdirectories (e.g. customers/, projects/) expose
        each subdirectory as a nested category (customers/andros).
        """
        categories: set[str] = set()

        if not self.base_path.is_dir():
            return []

        blocked = {b.lower() for b in BLOCKED_CATEGORIES}

        def _skip(name: str) -> bool:
            return name.startswith(".") or name.lower() in _HIDDEN_DIRS

        for item in self.base_path.iterdir():
            if not item.is_dir() or item.name.lower() in blocked or _skip(item.name):
                continue

            has_subdirs = False
            for sub in item.iterdir():
                if sub.is_dir() and not _skip(sub.name):
                    has_subdirs = True
                    categories.add(f"{item.name}/{sub.name}")

            if not has_subdirs or any(f.is_file() for f in item.iterdir()):
                categories.add(item.name)

        return sorted(categories)

    @staticmethod
    def _extract_category(relative_path: str) -> str:
        parts = relative_path.replace("\\", "/").strip("/").split("/")
        if len(parts) > 1:
            return parts[0]
        return "general"
