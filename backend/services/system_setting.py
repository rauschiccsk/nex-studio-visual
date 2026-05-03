"""Service layer for :class:`~backend.db.models.system_settings.SystemSetting`.

Runtime-mutable ICC-wide configuration. Known keys are registered in
:data:`DEFAULT_SETTINGS` so a fresh install resolves them without a
seed migration — the first time someone edits a value through the
Settings page, a row appears in ``system_settings`` and from then on
the DB value wins.

All methods accept ``db: Session`` as the first argument and only ever
call ``session.flush()`` — transaction commit is the router's
responsibility.

Beyond the CRUD, this module exposes typed helpers
(``get_str`` / ``get_int`` / ``get_float`` / ``get_bool``) that service
and route code uses to replace previously hard-coded constants. Each
helper is backed by a 30-second process-local cache so hot paths (port
validation, timeouts resolved per request) don't hit the DB on every
call; :func:`invalidate_cache` is called from :func:`upsert` so a
Settings-UI edit becomes visible immediately.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.system_settings import SystemSetting
from backend.schemas.system_setting import SystemSettingRead, SystemSettingValueType


@dataclass(frozen=True)
class _Default:
    """Service-layer default for a known setting key."""

    value: str
    description: str
    value_type: SystemSettingValueType = "string"


#: Known settings + their defaults. A row in ``system_settings`` overrides
#: these at runtime; when no row exists, the default is returned with
#: ``is_default=True``. Type hints are casted on read by the typed
#: helpers further down.
DEFAULT_SETTINGS: dict[str, _Default] = {
    # ── ICC ─────────────────────────────────────────────────────────
    "github_org": _Default(
        value="rauschiccsk",
        description=(
            "GitHub organisation used to auto-fill repository URLs on the new-project form as '{github_org}/{slug}'."
        ),
    ),
    # ── Pipeline / AI ───────────────────────────────────────────────
    "claude_stream_timeout_seconds": _Default(
        value="1800",
        value_type="int",
        description=(
            "Max wall-clock seconds a Claude CLI stream may run before "
            "the subprocess is killed. Raise if long re-emissions of "
            "the whole spec occasionally hit the cap."
        ),
    ),
    "claude_design_doc_timeout_seconds": _Default(
        value="1800",
        value_type="int",
        description=("Timeout for generating BEHAVIOR.md / DESIGN.md from an approved Vývojová dokumentácia."),
    ),
    "claude_task_plan_timeout_seconds": _Default(
        value="1800",
        value_type="int",
        description="Timeout for the Epic → Feat → Task plan generation.",
    ),
    "github_api_timeout_seconds": _Default(
        value="10",
        value_type="int",
        description="HTTP timeout for GitHub REST calls (repo probe, create, delete).",
    ),
    "conversation_history_limit": _Default(
        value="100",
        value_type="int",
        description=(
            "Max Architect conversation messages loaded as context — "
            "older messages are still persisted but not sent to the AI."
        ),
    ),
    "design_doc_max_chars": _Default(
        value="12000",
        value_type="int",
        description=(
            "Max characters of DESIGN.md handed to the feat-executor AI prompt so the context window is not blown."
        ),
    ),
    # ── Auth ────────────────────────────────────────────────────────
    "access_token_expire_minutes": _Default(
        value="480",
        value_type="int",
        description="JWT access-token lifetime in minutes (480 = 8h).",
    ),
    # ── Ports (ICC Port Registry v2 / D-020) ───────────────────────
    "port_range_min": _Default(
        value="10100",
        value_type="int",
        description=(
            "Lowest port number NEX Studio hands out to projects. "
            "Changing mid-flight is risky for existing deployments."
        ),
    ),
    "port_range_max": _Default(
        value="14999",
        value_type="int",
        description="Highest port number in the commercial-projects range.",
    ),
    "port_block_size": _Default(
        value="10",
        value_type="int",
        description=(
            "Port block size per project. D-020 reserved 10-port blocks (backend/frontend/db/ui-design + 6 spare)."
        ),
    ),
    # ── Path templates ─────────────────────────────────────────────
    "default_source_path_template": _Default(
        value="/opt/projects/{slug}",
        description=(
            "Default filesystem location where the project source is "
            "checked out. ``{slug}`` is substituted with the project slug. "
            "Convention per icc/STRUCTURE.md (2026-05-03): all new projects "
            "live under /opt/projects/<slug>/. Legacy projects in "
            "/opt/<slug>-src/ are migrated case-by-case."
        ),
    ),
    "default_kb_path_template": _Default(
        value="/home/icc/knowledge/projects/{slug}",
        description="Default KB directory for per-project live documents.",
    ),
    "template_init_script_path": _Default(
        value="/home/icc/knowledge/templates/claude-project/init.sh",
        description=(
            "Absolute path to the icc-claude-template init.sh bootstrap "
            "script. Invoked as subprocess on POST /api/v1/projects to "
            "auto-create the project directory + CLAUDE.md + skills + "
            "hooks + scripts. Override for self-hosted forks of the "
            "template. Empty string disables auto-bootstrap."
        ),
    ),
    "template_init_timeout_seconds": _Default(
        value="60",
        description=(
            "Maximum wall-clock seconds for template init.sh subprocess "
            "to complete. Typical greenfield bootstrap is < 5 s; the "
            "60 s default tolerates first-time mkdir + git init + "
            "Docker volume warmup."
        ),
        value_type="int",
    ),
    "reserved_port_ranges": _Default(
        value="10110-10159",
        description=(
            "Comma-separated list of port ranges reserved for projects "
            "managed externally (not via NEX Studio web UI). Format: "
            "'<start>-<end>,<start>-<end>,...'. _validate_ports rejects "
            "any port inside these ranges with HTTP 422. Default reserves "
            "10110-10159 for NEX Automat per D-022 (50-port mega-app "
            "allocation). Add new entries as ICC_STANDARDS evolves."
        ),
    ),
}


# ── Process-local cache for typed getters ─────────────────────────────

_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, tuple[str, str, float]] = {}
"""Map key → (value, value_type, expires_at_monotonic)."""


def invalidate_cache(key: Optional[str] = None) -> None:
    """Drop the cached value for *key* (or the whole cache if ``None``).

    Called from :func:`upsert` so a Settings-UI change takes effect on
    the next request rather than waiting up to ``_CACHE_TTL_SECONDS``.
    """
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


def _load_effective(db: Session, key: str) -> tuple[str, str]:
    """Return ``(value_str, value_type)`` for *key* — DB row if present,
    default otherwise. Callers are expected to have validated the key
    exists in :data:`DEFAULT_SETTINGS`."""
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is not None:
        return row.value, row.value_type

    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise KeyError(f"Unknown system setting: {key!r}")
    return default.value, default.value_type


def _cached(db: Session, key: str) -> tuple[str, str]:
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[2] > now:
        return cached[0], cached[1]
    value, value_type = _load_effective(db, key)
    _cache[key] = (value, value_type, now + _CACHE_TTL_SECONDS)
    return value, value_type


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on")


def get_str(db: Session, key: str) -> str:
    """Return the effective string value of *key* (cached)."""
    value, _ = _cached(db, key)
    return value


def get_int(db: Session, key: str) -> int:
    """Return the effective integer value of *key* (cached)."""
    value, _ = _cached(db, key)
    return int(value)


def get_float(db: Session, key: str) -> float:
    """Return the effective float value of *key* (cached)."""
    value, _ = _cached(db, key)
    return float(value)


def get_bool(db: Session, key: str) -> bool:
    """Return the effective bool value of *key* (cached).

    Accepts the usual truthy strings ``true/1/yes/on`` (case-insensitive).
    """
    value, _ = _cached(db, key)
    return _parse_bool(value)


# ── Read (router-facing) ─────────────────────────────────────────────


def _to_read_from_default(key: str, default: _Default) -> SystemSettingRead:
    return SystemSettingRead(
        key=key,
        value=default.value,
        value_type=default.value_type,
        description=default.description,
        updated_at=None,
        updated_by=None,
        updated_by_username=None,
        is_default=True,
    )


def _to_read_from_row(row: SystemSetting, username: Optional[str] = None) -> SystemSettingRead:
    return SystemSettingRead(
        key=row.key,
        value=row.value,
        value_type=row.value_type,
        description=row.description,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        updated_by_username=username,
        is_default=False,
    )


def _resolve_username(db: Session, user_id: Optional[UUID]) -> Optional[str]:
    """Return the ``username`` for a user id, or ``None`` if the user is
    missing (deleted or NULL on the row)."""
    if user_id is None:
        return None
    return db.execute(select(User.username).where(User.id == user_id)).scalar_one_or_none()


def list_all(db: Session) -> list[SystemSettingRead]:
    """Return every known setting.

    The result is the union of every key in :data:`DEFAULT_SETTINGS`
    plus any row in ``system_settings`` that does not correspond to a
    registered default (forward-compat for admin-inserted keys). DB
    rows override defaults; missing keys fall back to the default.

    Ordered by key ASC so the Settings page renders a stable list.
    """
    stored: dict[str, SystemSetting] = {row.key: row for row in db.execute(select(SystemSetting)).scalars()}
    keys = sorted(set(DEFAULT_SETTINGS.keys()) | set(stored.keys()))
    out: list[SystemSettingRead] = []
    for key in keys:
        row = stored.get(key)
        if row is not None:
            out.append(_to_read_from_row(row, _resolve_username(db, row.updated_by)))
            continue
        default = DEFAULT_SETTINGS.get(key)
        if default is not None:
            out.append(_to_read_from_default(key, default))
    return out


def get_by_key(db: Session, key: str) -> SystemSettingRead:
    """Return one setting by key — DB row if present, default otherwise.

    Raises :class:`ValueError` if the key is neither stored nor in
    :data:`DEFAULT_SETTINGS`.
    """
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is not None:
        return _to_read_from_row(row, _resolve_username(db, row.updated_by))

    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise ValueError(f"Unknown system setting: {key!r}")
    return _to_read_from_default(key, default)


def _validate_value_for_type(value: str, value_type: SystemSettingValueType) -> None:
    """Raise :class:`ValueError` when *value* cannot be parsed as *value_type*."""
    if value_type == "string":
        return
    if value_type == "int":
        try:
            int(value)
        except ValueError as exc:
            raise ValueError(f"Value {value!r} is not a valid int") from exc
        return
    if value_type == "float":
        try:
            float(value)
        except ValueError as exc:
            raise ValueError(f"Value {value!r} is not a valid float") from exc
        return
    if value_type == "bool":
        if value.strip().lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValueError(f"Value {value!r} is not a valid bool — use true/false/1/0/yes/no/on/off")


def upsert(
    db: Session,
    key: str,
    value: str,
    *,
    updated_by: Optional[UUID] = None,
) -> SystemSettingRead:
    """Create or update a setting row.

    Only keys registered in :data:`DEFAULT_SETTINGS` may be upserted —
    unknown keys are rejected so the Settings UI cannot drift from the
    backend's list of recognised settings. The row's ``description``
    and ``value_type`` are sourced from the default when creating;
    updates keep the stored description untouched.

    Raises :class:`ValueError` when ``key`` is unknown or when
    ``value`` cannot be parsed against the registered ``value_type``.
    """
    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise ValueError(f"Unknown system setting: {key!r}")

    _validate_value_for_type(value, default.value_type)

    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()

    if row is None:
        row = SystemSetting(
            key=key,
            value=value,
            value_type=default.value_type,
            description=default.description,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.value = value
        # Keep value_type in sync with the registered default —
        # defensive against ``DEFAULT_SETTINGS`` evolution.
        row.value_type = default.value_type
        row.updated_by = updated_by

    db.flush()
    invalidate_cache(key)
    return _to_read_from_row(row, _resolve_username(db, row.updated_by))
