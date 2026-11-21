"""DEV-453 — a ticket a version's Zadanie names shows that version and is not offered „Spustiť rýchlu opravu“.

20.11.2026 NEX HelpDesk: DEV-434 was fixed in 0.1.1 (a fast fix started from the project's dialog, the ticket named in
its directive) and DEV-412 was the Zadanie of the first version. Only a fast fix started at the ticket links a version
to it, so Plane tikety offered a fast fix for both and the Manažér asked whether to join three fast fixes into one.
"""

from __future__ import annotations

from backend.db.models.pipeline import PipelineMessage
from backend.services import plane_tickets
from tests import test_the_plane_tickets_tab_shows_the_projects_tickets as tab
from tests.test_backlog import _make_version

#: The Plane tickets tab's fixtures: a fake Plane and a project with its module and tickets DEV-11 … DEV-16.
plane = tab.plane
setup = tab.setup


def _fast_fix(db, version, directive: str) -> None:
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="kickoff",
            content=directive,
            status="delivered",
            payload={"flow_type": "fast_fix", "phase": "priprava", "directive": directive},
        )
    )
    db.flush()


def _zadanie(root, number: str, text: str) -> None:
    doc = root / plane_tickets.CUSTOMER_REQUIREMENTS.format(number=number)
    doc.parent.mkdir(parents=True)
    doc.write_text(text, encoding="utf-8")


def _rows(client, project):
    body = client.get(f"/api/v1/projects/{project.slug}/plane-tickets").json()
    return {t["identifier"]: (t["named_in_version"], t["fix_version"]) for t in body["tickets"]}


def test_a_ticket_named_in_a_fast_fixs_directive_or_a_versions_zadanie_shows_that_version(
    setup,
    db_session,
    tmp_path,
):
    _owner, project, client = setup
    project.source_path = str(tmp_path / "p")
    first = _make_version(db_session, project)
    _zadanie(tmp_path / "p", first.version_number, "# Zadanie\n\nTiket DEV-12 — prvá verzia aplikácie.\n")
    fix = _make_version(db_session, project)
    _fast_fix(db_session, fix, "Oprava podľa tiketu DEV-11: hovor sa nespojí.")
    db_session.flush()

    assert _rows(client, project) == {
        "DEV-12": (f"v{first.version_number}", None),
        "DEV-11": (f"v{fix.version_number}", None),
    }


def test_a_renamed_version_reads_its_zadanie_under_the_number_it_was_built_as(setup, db_session, tmp_path):
    _owner, project, client = setup
    project.source_path = str(tmp_path / "p")
    version = _make_version(db_session, project)
    _zadanie(tmp_path / "p", "0.2.0", "Tikety: DEV-11.\n")
    version.graduated_from, version.version_number = "0.2.0", "1.0.0"
    db_session.flush()

    assert _rows(client, project)["DEV-11"] == ("v1.0.0", None)


def test_a_ticket_nothing_names_is_offered_as_before_and_a_longer_number_is_not_it(setup, db_session, tmp_path):
    _owner, project, client = setup
    project.source_path = str(tmp_path / "p")
    version = _make_version(db_session, project)
    _zadanie(tmp_path / "p", version.version_number, "Súvisí s DEV-112, nexDEV-12 a ICC-DEV-11.\n")
    db_session.flush()

    assert _rows(client, project) == {"DEV-12": (None, None), "DEV-11": (None, None)}
