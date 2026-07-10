"""Service-level tests for the system_settings read path (Nastavenia Fáza 2).

Covers the load-bearing invariant of :func:`system_setting._to_read_from_row` /
:func:`system_setting._to_read_from_default`: the Slovak ``label`` / ``unit`` /
``description`` are REGISTRY metadata (``DEFAULT_SETTINGS``), never sourced from the
stored row. A runtime override therefore still shows the current Slovak
label/description/unit — not a stale ``row.description`` written before this change.
"""

from __future__ import annotations

from backend.db.models.system_settings import SystemSetting
from backend.services import system_setting as svc

# Registry (Slovak) metadata for the sample key — see DEFAULT_SETTINGS.
_KEY = "claude_design_doc_timeout_seconds"
_LABEL = "Časový limit generovania návrhovej dokumentácie"
_UNIT = "sekúnd"
_DESCRIPTION = (
    "Časový limit na vygenerovanie návrhových dokumentov (BEHAVIOR.md / DESIGN.md) "
    "zo schválenej vývojovej dokumentácie."
)


def test_read_from_default_carries_slovak_label_unit_description(db_session):
    """No stored row → default read carries the registry Slovak label/unit/description."""
    read = svc.get_by_key(db_session, _KEY)

    assert read.is_default is True
    assert read.value == "1800"  # unchanged registry default value
    assert read.label == _LABEL
    assert read.unit == _UNIT
    assert read.description == _DESCRIPTION


def test_read_from_overridden_row_still_uses_registry_metadata(db_session):
    """A stored override keeps its own value but shows the registry Slovak label/unit/description.

    The row is seeded with a deliberately STALE English ``description`` to prove the
    read path ignores ``row.description`` in favour of the registry.
    """
    db_session.add(
        SystemSetting(
            key=_KEY,
            value="3600",
            value_type="int",
            description="STALE English description written before Fáza 2",
        )
    )
    db_session.flush()

    read = svc.get_by_key(db_session, _KEY)

    assert read.is_default is False
    assert read.value == "3600"  # override value comes from the row
    # label / unit / description are registry metadata, NOT from the stale row.
    assert read.label == _LABEL
    assert read.unit == _UNIT
    assert read.description == _DESCRIPTION
    assert read.description != "STALE English description written before Fáza 2"


def test_unknown_admin_inserted_key_falls_back_to_row(db_session):
    """A row with no registered default → label falls back to the key, unit empty, description from the row."""
    db_session.add(
        SystemSetting(
            key="admin_inserted_unknown_key",
            value="whatever",
            value_type="string",
            description="Admin note",
        )
    )
    db_session.flush()

    read = svc.get_by_key(db_session, "admin_inserted_unknown_key")

    assert read.is_default is False
    assert read.label == "admin_inserted_unknown_key"
    assert read.unit == ""
    assert read.description == "Admin note"
