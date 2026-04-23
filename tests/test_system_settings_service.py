"""Tests for :mod:`backend.services.system_setting`.

Covers:

* ``get_by_key`` returns the default when no DB row exists
  (``is_default=True``) and the stored row otherwise.
* ``upsert`` creates the row on first call, updates on subsequent
  calls, and rejects unknown keys.
* ``list_all`` merges defaults with stored overrides, sorted by key.
* Unknown keys raise ``ValueError`` at both read and write.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.db.models.foundation import User
from backend.db.models.system_settings import SystemSetting
from backend.services import system_setting as service


def _make_user(db_session: Any) -> User:
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


# ── defaults ──────────────────────────────────────────────────────────


def test_default_github_org_is_rauschiccsk() -> None:
    """The only registered default today — used by the new-project form."""
    assert service.DEFAULT_SETTINGS["github_org"].value == "rauschiccsk"


def test_get_by_key_returns_default_when_no_row(db_session: Any) -> None:
    result = service.get_by_key(db_session, "github_org")
    assert result.key == "github_org"
    assert result.value == "rauschiccsk"
    assert result.is_default is True
    assert result.updated_at is None
    assert result.updated_by is None
    assert result.updated_by_username is None


def test_get_by_key_unknown_raises(db_session: Any) -> None:
    with pytest.raises(ValueError, match="Unknown system setting"):
        service.get_by_key(db_session, "not-registered")


# ── upsert ────────────────────────────────────────────────────────────


def test_upsert_creates_row_on_first_call(db_session: Any) -> None:
    user = _make_user(db_session)
    result = service.upsert(
        db_session, "github_org", "isnex-official", updated_by=user.id
    )

    assert result.key == "github_org"
    assert result.value == "isnex-official"
    assert result.is_default is False
    assert result.updated_by == user.id
    assert result.updated_by_username == user.username
    assert result.updated_at is not None

    # Row really exists in the DB.
    from sqlalchemy import select as sa_select

    stored = db_session.execute(
        sa_select(SystemSetting).where(SystemSetting.key == "github_org")
    ).scalar_one()
    assert stored.value == "isnex-official"


def test_upsert_updates_existing_row(db_session: Any) -> None:
    user = _make_user(db_session)
    service.upsert(db_session, "github_org", "first-value", updated_by=user.id)
    result = service.upsert(
        db_session, "github_org", "second-value", updated_by=user.id
    )

    assert result.value == "second-value"
    assert result.is_default is False


def test_upsert_unknown_key_raises(db_session: Any) -> None:
    with pytest.raises(ValueError, match="Unknown system setting"):
        service.upsert(db_session, "unregistered", "anything")


def test_get_by_key_returns_stored_row_after_upsert(db_session: Any) -> None:
    user = _make_user(db_session)
    service.upsert(db_session, "github_org", "custom-org", updated_by=user.id)

    result = service.get_by_key(db_session, "github_org")
    assert result.value == "custom-org"
    assert result.is_default is False


# ── list_all ──────────────────────────────────────────────────────────


def test_list_all_returns_defaults_when_db_empty(db_session: Any) -> None:
    result = service.list_all(db_session)
    keys = [s.key for s in result]
    # At minimum github_org is registered.
    assert "github_org" in keys
    github = next(s for s in result if s.key == "github_org")
    assert github.is_default is True
    assert github.value == "rauschiccsk"


def test_list_all_merges_overrides(db_session: Any) -> None:
    user = _make_user(db_session)
    service.upsert(db_session, "github_org", "my-fork", updated_by=user.id)

    result = service.list_all(db_session)
    github = next(s for s in result if s.key == "github_org")
    assert github.is_default is False
    assert github.value == "my-fork"


def test_list_all_sorted_by_key(db_session: Any) -> None:
    result = service.list_all(db_session)
    keys = [s.key for s in result]
    assert keys == sorted(keys)
