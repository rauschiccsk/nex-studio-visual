"""Service layer for :class:`~backend.db.models.system_settings.SystemSetting`.

Runtime-mutable ICC-wide configuration. Known keys are registered in
:data:`DEFAULT_SETTINGS` so a fresh install resolves them without a
seed migration — the first time someone edits a value through the
Settings page, a row appears in ``system_settings`` and from then on
the DB value wins.

All methods accept ``db: Session`` as the first argument and only ever
call ``session.flush()`` — transaction commit is the router's
responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.system_settings import SystemSetting
from backend.schemas.system_setting import SystemSettingRead


@dataclass(frozen=True)
class _Default:
    """Service-layer default for a known setting key."""

    value: str
    description: str


#: Known settings + their defaults. A row in ``system_settings`` overrides
#: these at runtime; when no row exists, the default is returned with
#: ``is_default=True``.
DEFAULT_SETTINGS: dict[str, _Default] = {
    "github_org": _Default(
        value="rauschiccsk",
        description=(
            "GitHub organisation used to auto-fill repository URLs on "
            "the new-project form as '{github_org}/{slug}'."
        ),
    ),
}


def _to_read_from_default(key: str, default: _Default) -> SystemSettingRead:
    return SystemSettingRead(
        key=key,
        value=default.value,
        description=default.description,
        updated_at=None,
        updated_by=None,
        updated_by_username=None,
        is_default=True,
    )


def _to_read_from_row(
    row: SystemSetting, username: Optional[str] = None
) -> SystemSettingRead:
    return SystemSettingRead(
        key=row.key,
        value=row.value,
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
    return db.execute(
        select(User.username).where(User.id == user_id)
    ).scalar_one_or_none()


def list_all(db: Session) -> list[SystemSettingRead]:
    """Return every known setting.

    The result is the union of every key in :data:`DEFAULT_SETTINGS`
    plus any row in ``system_settings`` that does not correspond to a
    registered default (forward-compat for admin-inserted keys). DB
    rows override defaults; missing keys fall back to the default.

    Ordered by key ASC so the Settings page renders a stable list.
    """
    stored: dict[str, SystemSetting] = {
        row.key: row for row in db.execute(select(SystemSetting)).scalars()
    }
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
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    ).scalar_one_or_none()
    if row is not None:
        return _to_read_from_row(row, _resolve_username(db, row.updated_by))

    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise ValueError(f"Unknown system setting: {key!r}")
    return _to_read_from_default(key, default)


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
    is sourced from the default when creating; updates keep the stored
    description untouched.

    Raises :class:`ValueError` when ``key`` is unknown.
    """
    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise ValueError(f"Unknown system setting: {key!r}")

    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    ).scalar_one_or_none()

    if row is None:
        row = SystemSetting(
            key=key,
            value=value,
            description=default.description,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.value = value
        row.updated_by = updated_by

    db.flush()
    return _to_read_from_row(row, _resolve_username(db, row.updated_by))
