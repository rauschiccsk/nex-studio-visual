"""Pure v1→v2 row transforms (no DB access — 100% unit-testable).

The v2 schema DIVERGED from v1 at migrations 068-072 (it is not an additive
superset). These pure functions encode the bridge for the columns that changed
or were added, so the copier stays a thin read→transform→insert loop.

Divergences handled here (verified against the models + migrations 069/070/072):

* ``projects.category`` ('singlemodule'/'multimodule') → ``projects.type``
  ('standard'/'web'). CHECK ``ck_projects_type`` REJECTS any raw v1 value, so the
  map is MANDATORY (projects.py:34/87-90).
* ``projects.auth_mode`` — new NOT NULL column; backfill 'password' (projects.py:39/91-94).
* ``projects.source_path`` rebased ``/opt/projects/<slug>`` → ``<projects_root>/<slug>``.
* The remaining v2-added project columns get safe defaults (miera_autonomie/uat_slug
  NULL, guardian_enabled/custom_development_enabled False, owner_id ← creator).

The per-table ``plain_description`` (epics/feats/tasks, migration 080) and
``tasks.baseline_sha`` (nullable) are always absent in v1 → the copier sets them
NULL inline; they are trivial and per-table, so they are not folded into the
project-shaped :func:`new_column_defaults` below.
"""

from __future__ import annotations

from collections.abc import Mapping
from posixpath import join as posix_join

# v1 ``projects.category`` values (v1 main projects.py:26). Both collapse to
# 'standard' in v2 — the multi-module CORE was dropped (migration 070); epics are
# already project-level via ``version_id``, so a flattened 'standard' is faithful.
_CATEGORY_TO_TYPE = {
    "singlemodule": "standard",
    "multimodule": "standard",
    # A v2-shaped source (or a project already typed 'web'/'standard') passes through.
    "standard": "standard",
    "web": "web",
}

# The v2 default auth mode for a migrated project — v1 had no auth_mode column.
_DEFAULT_AUTH_MODE = "password"

# Prefix rebased onto ``projects_root`` for ``projects.source_path``.
_V1_PROJECTS_PREFIX = "/opt/projects/"


def map_category_to_type(category: str | None) -> str:
    """Map a v1 ``projects.category`` to a v2 ``projects.type``.

    ``singlemodule``/``multimodule`` → ``standard``; ``web`` → ``web``; anything
    unknown or NULL → ``standard`` (the safe default that always satisfies
    ``ck_projects_type``). Never returns a value the CHECK would reject.
    """
    if category is None:
        return "standard"
    return _CATEGORY_TO_TYPE.get(category.strip().lower(), "standard")


def default_auth_mode() -> str:
    """Return the backfill value for the v2-mandatory ``projects.auth_mode``."""
    return _DEFAULT_AUTH_MODE


def rewrite_source_path(old: str | None, projects_root: str) -> str | None:
    """Rebase a v1 ``source_path`` from ``/opt/projects/`` onto ``projects_root``.

    ``/opt/projects/nex-inbox`` → ``<projects_root>/nex-inbox``. A NULL path stays
    NULL; a path that does not start with the v1 prefix is returned unchanged (the
    tool never invents a location it cannot derive).
    """
    if old is None:
        return None
    if old.startswith(_V1_PROJECTS_PREFIX):
        rest = old[len(_V1_PROJECTS_PREFIX) :]
        return posix_join(projects_root, rest)
    return old


def new_column_defaults(source_row: Mapping) -> dict:
    """Compute the v2-added PROJECT columns for a migrated project.

    Reads from a source ``projects`` row mapping (reflection-tolerant — uses
    ``.get`` so a v1 source that lacks a column, or a v2-shaped source that has it,
    both work). Returns the columns the v2 ``projects`` table adds over v1:

    * ``type`` — mapped from v1 ``category`` (or a pass-through ``type``).
    * ``auth_mode`` — backfilled 'password' (or a pass-through explicit value).
    * ``miera_autonomie`` / ``uat_slug`` — NULL (v2-added; inherit global / no UAT).
    * ``guardian_enabled`` / ``custom_development_enabled`` — False (v2-added flags).
    * ``owner_id`` — the source owner if present, else the creator (SET NULL→creator).
    """
    created_by = source_row.get("created_by")
    return {
        "type": map_category_to_type(source_row.get("category") or source_row.get("type")),
        "auth_mode": source_row.get("auth_mode") or default_auth_mode(),
        "miera_autonomie": source_row.get("miera_autonomie"),
        "uat_slug": source_row.get("uat_slug"),
        "guardian_enabled": bool(source_row.get("guardian_enabled") or False),
        "custom_development_enabled": bool(source_row.get("custom_development_enabled") or False),
        "owner_id": source_row.get("owner_id") or created_by,
    }
