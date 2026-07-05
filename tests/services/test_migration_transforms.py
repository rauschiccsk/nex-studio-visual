"""Unit tests for the pure migration transforms + the existence-only secrets guard.

No DB access — these cover ``backend.services.migration.transforms`` (category→type
map, auth_mode backfill, source_path rewrite, all v2-added project defaults) and
``backend.services.migration.secrets_guard`` (existence-only; content NEVER read,
``read_content`` never called).
"""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path

import pytest

from backend.services.migration import secrets_guard
from backend.services.migration.secrets_guard import credential_file_present
from backend.services.migration.transforms import (
    default_auth_mode,
    map_category_to_type,
    new_column_defaults,
    rewrite_source_path,
)

# ---------------------------------------------------------------------------
# map_category_to_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("singlemodule", "standard"),
        ("multimodule", "standard"),
        ("web", "web"),
        ("standard", "standard"),  # a v2-shaped/pass-through source
        (None, "standard"),  # NULL → safe default
        ("garbage", "standard"),  # unknown → safe default (never rejected by CHECK)
        ("MULTIMODULE", "standard"),  # case-insensitive
        ("  web  ", "web"),  # whitespace-tolerant
    ],
)
def test_map_category_to_type(category, expected):
    assert map_category_to_type(category) == expected


def test_map_category_to_type_never_returns_invalid_value():
    # ck_projects_type only accepts 'standard'/'web' — every mapping must be in-set.
    for raw in ("singlemodule", "multimodule", "web", "standard", None, "weird", ""):
        assert map_category_to_type(raw) in ("standard", "web")


# ---------------------------------------------------------------------------
# default_auth_mode
# ---------------------------------------------------------------------------


def test_default_auth_mode_is_password():
    assert default_auth_mode() == "password"


# ---------------------------------------------------------------------------
# rewrite_source_path
# ---------------------------------------------------------------------------


def test_rewrite_source_path_rebases_v1_prefix():
    assert rewrite_source_path("/opt/projects/nex-inbox", "/opt/projects-v2") == "/opt/projects-v2/nex-inbox"


def test_rewrite_source_path_honours_custom_root():
    assert rewrite_source_path("/opt/projects/alpha", "/srv/apps") == "/srv/apps/alpha"


def test_rewrite_source_path_null_stays_null():
    assert rewrite_source_path(None, "/opt/projects-v2") is None


def test_rewrite_source_path_non_matching_prefix_unchanged():
    # The tool never invents a location it cannot derive from the v1 prefix.
    assert rewrite_source_path("/custom/location/x", "/opt/projects-v2") == "/custom/location/x"


# ---------------------------------------------------------------------------
# new_column_defaults
# ---------------------------------------------------------------------------


def test_new_column_defaults_full_v1_row():
    creator = uuid.uuid4()
    src = {"category": "multimodule", "created_by": creator}
    d = new_column_defaults(src)
    assert d["type"] == "standard"
    assert d["auth_mode"] == "password"
    assert d["miera_autonomie"] is None
    assert d["uat_slug"] is None
    assert d["guardian_enabled"] is False
    assert d["custom_development_enabled"] is False
    # owner_id falls back to the creator when the v1 source has no owner_id.
    assert d["owner_id"] == creator


def test_new_column_defaults_owner_id_preserved_when_present():
    creator = uuid.uuid4()
    owner = uuid.uuid4()
    src = {"category": "singlemodule", "created_by": creator, "owner_id": owner}
    d = new_column_defaults(src)
    assert d["owner_id"] == owner  # explicit owner wins over the creator fallback
    assert d["type"] == "standard"


def test_new_column_defaults_owner_id_falls_back_on_null():
    creator = uuid.uuid4()
    src = {"category": "web", "created_by": creator, "owner_id": None}
    d = new_column_defaults(src)
    assert d["owner_id"] == creator
    assert d["type"] == "web"


def test_new_column_defaults_passthrough_type_and_auth_mode():
    # A v2-shaped source that already carries type/auth_mode is respected.
    creator = uuid.uuid4()
    src = {"type": "web", "auth_mode": "token", "created_by": creator}
    d = new_column_defaults(src)
    assert d["type"] == "web"
    assert d["auth_mode"] == "token"


# ---------------------------------------------------------------------------
# secrets_guard — existence-only, content NEVER read
# ---------------------------------------------------------------------------


def test_credential_file_present_true_for_existing_file(tmp_path: Path):
    f = tmp_path / "cred.md"
    f.write_text("SECRET_VALUE=hunter2", encoding="utf-8")
    assert credential_file_present(str(f)) is True


def test_credential_file_present_false_for_missing_file(tmp_path: Path):
    assert credential_file_present(str(tmp_path / "nope.md")) is False


def test_credential_file_present_false_for_none_or_empty():
    assert credential_file_present(None) is False
    assert credential_file_present("") is False


def test_credential_file_present_false_for_directory(tmp_path: Path):
    # A directory is not a regular file.
    assert credential_file_present(str(tmp_path)) is False


def test_secrets_guard_never_returns_or_reads_content(tmp_path: Path):
    # The function returns a bool, never content — even for a file full of secrets.
    secret = "TOKEN=super-secret-do-not-leak"
    f = tmp_path / "leaky.md"
    f.write_text(secret, encoding="utf-8")
    result = credential_file_present(str(f))
    assert result is True
    assert result is not secret  # bool, not the content
    assert isinstance(result, bool)


def test_secrets_guard_module_never_imports_or_calls_read_content():
    """Static guard: the source of secrets_guard must not read file content.

    It must not import the credentials service nor call any byte-reading API. This
    complements the ``grep`` verification in the task list (which requires the
    module to be free of ``read_content``/``read_bytes``/``read_text``).
    """
    source = inspect.getsource(secrets_guard)
    for forbidden in ("read_content", "read_bytes", "read_text", ".open(", "open("):
        assert forbidden not in source, f"secrets_guard must not reference {forbidden!r}"
    # Must not import the credentials service (its content API decodes secret bytes).
    assert "backend.services.credentials" not in source
    assert "import credentials" not in source
