"""Service-level tests for the system_settings read path (Nastavenia Fáza 2).

Covers the load-bearing invariant of :func:`system_setting._to_read_from_row` /
:func:`system_setting._to_read_from_default`: the Slovak ``label`` / ``unit`` /
``description`` are REGISTRY metadata (``DEFAULT_SETTINGS``), never sourced from the
stored row. A runtime override therefore still shows the current Slovak
label/description/unit — not a stale ``row.description`` written before this change.
"""

from __future__ import annotations

import pytest

from backend.db.models.system_settings import SystemSetting
from backend.services import system_setting as svc

# Registry (Slovak) metadata for the sample key — see DEFAULT_SETTINGS.
# Deliberately a key some code actually READS (``backend.api.routes.projects``
# resolves it per request): the previous sample was
# ``claude_design_doc_timeout_seconds``, one of the five dead "Priebeh / AI"
# dials, so this suite proved the metadata plumbing using a setting that
# controlled nothing. It has since been retired — see
# ``test_retired_pipeline_ai_settings`` below.
_KEY = "github_api_timeout_seconds"
_LABEL = "Časový limit GitHub API"
_UNIT = "sekúnd"
_DESCRIPTION = "Časový limit HTTP volaní na GitHub (overenie, vytvorenie a zmazanie repozitára)."


def test_read_from_default_carries_slovak_label_unit_description(db_session):
    """No stored row → default read carries the registry Slovak label/unit/description."""
    read = svc.get_by_key(db_session, _KEY)

    assert read.is_default is True
    assert read.value == "10"  # unchanged registry default value
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


# ---------------------------------------------------------------------------
# The retired "Priebeh / AI" category
# ---------------------------------------------------------------------------

#: The five keys that made up the whole "Priebeh / AI" settings category. Each had a
#: Slovak label, a unit and a description promising a concrete effect, and NOT ONE was
#: read by any code — a settings screen wired to nothing. Removed rather than wired up
#: because the consumers their descriptions named no longer exist in v4 (see the note in
#: ``system_setting.DEFAULT_SETTINGS``).
RETIRED_PIPELINE_AI_KEYS = (
    "claude_stream_timeout_seconds",
    "claude_design_doc_timeout_seconds",
    "claude_task_plan_timeout_seconds",
    "conversation_history_limit",
    "design_doc_max_chars",
)

#: Prefixes the frontend maps to the "Priebeh / AI" category (SettingsPage.tsx
#: ``SETTINGS_CATEGORIES``). No registered key may match them again: the panel only
#: draws categories that have at least one setting, so an empty match set is what keeps
#: the dead section off the screen instead of showing dials that do nothing.
_PIPELINE_AI_PREFIXES = ("claude_", "conversation_", "design_doc_")


def test_retired_pipeline_ai_settings_are_not_registered():
    for key in RETIRED_PIPELINE_AI_KEYS:
        assert key not in svc.DEFAULT_SETTINGS, f"{key} was retired as dead — re-register it only WITH a consumer"


def test_no_registered_key_falls_into_the_dead_pipeline_ai_category():
    offenders = [key for key in svc.DEFAULT_SETTINGS if any(key.startswith(prefix) for prefix in _PIPELINE_AI_PREFIXES)]
    assert offenders == [], (
        f"{offenders} would render under the 'Priebeh / AI' category, whose description promises "
        "Claude timeouts and AI context limits. Wire the key to that consumer or name it for the "
        "category it truly belongs to."
    )


def test_retired_keys_are_rejected_by_upsert(db_session):
    """A retired key must not be storable — otherwise the Settings UI could recreate the dial."""
    for key in RETIRED_PIPELINE_AI_KEYS:
        with pytest.raises(ValueError):
            svc.upsert(db_session, key, "1234")


def test_retired_keys_are_absent_from_the_settings_listing(db_session):
    """``list_all`` backs the Settings page — the dead dials must not appear on it."""
    listed = {row.key for row in svc.list_all(db_session)}
    assert listed.isdisjoint(RETIRED_PIPELINE_AI_KEYS)


def test_surviving_github_timeout_is_still_registered_and_readable(db_session):
    """The one key from that block with a real consumer stays — and keeps working.

    ``backend.api.routes.projects`` resolves it per request via ``get_int``; retiring the
    dead neighbours must not take it with them.
    """
    assert "github_api_timeout_seconds" in svc.DEFAULT_SETTINGS
    assert svc.get_int(db_session, "github_api_timeout_seconds") == 10
