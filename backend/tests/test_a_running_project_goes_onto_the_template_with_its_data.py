"""DEV-287 — a project that runs at a customer goes onto the app template, and its version carries its data along.

The cockpit converted only a project that ran nowhere (DEV-254, NEX Knowledge 0.2.0) and refused one with an
installation: its people, sessions and settings would have had to move into the frame's tables and nothing said how.
NEX Websites and NEX Manager run at customers. Director 02.11.2026: „Súhlasím“ — the conversion is allowed, and the
Príprava of the version gets the transfer of data as a regular item of its scope.
"""

from __future__ import annotations

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import _make_version

FRAME = next(s for s in orchestrator.ICC_STANDARDS if s.key == "ram_sablony")
UPDATES = next(s for s in orchestrator.ICC_STANDARDS if s.key == "aktualizacie")


def _deployed(db, project, status="ok"):
    customer = Customer(project_id=project.id, name="ICC", slug=f"icc-{status}")
    db.add(customer)
    db.flush()
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project.id,
            version_number="1.0.0",
            environment="prod",
            event_type="deploy",
            status=status,
        )
    )
    db.flush()


def test_the_transfer_joins_the_frame_only_where_the_project_runs(monkeypatch):
    monkeypatch.setattr(orchestrator, "missing_icc_standards", lambda root: [FRAME])

    running = orchestrator._icc_standards_scope_block("nex-websites", installed=True)
    nowhere = orchestrator._icc_standards_scope_block("nex-websites", installed=False)

    assert f"  - {orchestrator.DATA_TRANSFER_SENTENCE}" in running
    assert FRAME.scope_sentence in running and FRAME.scope_sentence in nowhere
    assert orchestrator.DATA_TRANSFER_SENTENCE not in nowhere


def test_no_transfer_without_a_frame_to_go_onto(monkeypatch):
    monkeypatch.setattr(orchestrator, "missing_icc_standards", lambda root: [UPDATES])

    assert orchestrator.DATA_TRANSFER_SENTENCE not in orchestrator._icc_standards_scope_block("x", installed=True)


def test_the_priprava_of_a_running_project_asks_for_the_transfer(db_session, monkeypatch):
    monkeypatch.setattr(orchestrator, "missing_icc_standards", lambda root: [FRAME])
    version, project = _make_version(db_session, project_dial="plna")

    before = orchestrator._priprava_directive(db_session, version.id)
    _deployed(db_session, project, status="failed")
    failed = orchestrator._priprava_directive(db_session, version.id)
    _deployed(db_session, project)
    running = orchestrator._priprava_directive(db_session, version.id)

    assert orchestrator.DATA_TRANSFER_SENTENCE not in before
    assert orchestrator.DATA_TRANSFER_SENTENCE not in failed, "a deploy that did not go through left nothing to carry"
    assert orchestrator.DATA_TRANSFER_SENTENCE in running


def test_the_transfer_names_what_moves_what_goes_and_what_stays():
    sentence = orchestrator.DATA_TRANSFER_SENTENCE

    for said in ("kit_users", "kit_system_settings", "relácie a použité lístky", "NEX Managera", "zostanú", "zálohuje"):
        assert said in sentence, said
