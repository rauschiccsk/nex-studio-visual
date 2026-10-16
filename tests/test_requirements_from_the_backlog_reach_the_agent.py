"""DEV-134 — a requirement the Manažér assigns to a version from the Zásobník reaches the build agent.

16.10.2026, preparing NEX Inbox 1.9.0 (five tickets from Plane through the Zásobník into one version): the requirements
assigned to a version were put in front of the agent only at the stage „gate_a“ — a stage of the retired v1 pipeline.
Today's Príprava takes ``_priprava_directive``, which never read the Zásobník. Whoever assigned a requirement to a
version believed the agent would build it; the agent never heard of it. No test guarded the way.

The rule: the brief the agent starts Príprava with carries every requirement assigned to its version — number, title,
description and its ticket in Plane — and nothing that is not assigned to it.
"""

from __future__ import annotations

import pytest

from backend.db.models.backlog import BacklogItem
from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import _make_version


@pytest.fixture
def version(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session, project_dial="plna")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    return version, project


def _requirement(db, project, number, title, *, version=None, status="open", **extra):
    item = BacklogItem(
        project_id=project.id,
        number=number,
        title=title,
        status=status,
        version_id=version.id if version is not None else None,
        **extra,
    )
    db.add(item)
    db.flush()
    return item


def test_the_priprava_brief_carries_the_requirements_assigned_to_the_version(db_session, version):
    version, project = version
    _requirement(
        db_session,
        project,
        5,
        "Výpadok schránky sa ohlási",
        version=version,
        status="included",
        description="Kontrola imap v /health hlási degraded.",
        plane_identifier="DEV-131",
    )
    _requirement(db_session, project, 6, "Párovanie dodávateľa podľa IČ DPH", version=version, status="included")
    _requirement(db_session, project, 7, "Ešte nepriradená požiadavka")

    brief = orchestrator._priprava_directive(db_session, version.id)

    assert "REQ-5: Výpadok schránky sa ohlási — Kontrola imap v /health hlási degraded. (tiket DEV-131)" in brief
    assert "REQ-6: Párovanie dodávateľa podľa IČ DPH" in brief
    assert "Ešte nepriradená požiadavka" not in brief


def test_a_version_without_assigned_requirements_gets_no_empty_heading(db_session, version):
    version, project = version
    _requirement(db_session, project, 1, "Nepriradená")
    assert orchestrator.BACKLOG_HEADING not in orchestrator._priprava_directive(db_session, version.id)


def test_requirements_of_another_version_stay_out(db_session, version):
    version, project = version
    from backend.db.models.versions import Version

    ina = Version(project_id=project.id, version_number="9.9.9", name="iná")
    db_session.add(ina)
    db_session.flush()
    _requirement(db_session, project, 2, "Patrí inej verzii", version=ina, status="included")
    assert "Patrí inej verzii" not in orchestrator._priprava_directive(db_session, version.id)


def test_a_rejected_requirement_stays_out_even_with_the_version_on_it(db_session, version):
    version, project = version
    _requirement(db_session, project, 3, "Zamietnutá po priradení", version=version, status="rejected")
    assert "Zamietnutá po priradení" not in orchestrator._priprava_directive(db_session, version.id)
