"""A saved Zadanie can be edited — the guard refuses only a write over text the editor never saw (DEV-40).

09.10.2026, Career Asistent: measured on ``write_zadanie`` itself — save „Zadanie v1", then save it with one more
sentence → refused (409). The ICCINT-71 guard compared the new text with the disk and refused ANY difference,
and the cockpit never sends ``replace_existing``: a saved Zadanie could not be edited at all. Director: „Áno,
rozšír DEV-40 a oprav to celé".

The guard exists for one case — writing over text the Manažér never saw (07.09.2026: 71 lines lost under one
sentence). An edit of the text the editor loaded is not that case. So the editor says which text it started
from (``based_on``); the write goes through when the disk still holds exactly that, and is refused — with what
is there — when the disk changed meanwhile or the editor saw nothing.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.routes import versions as versions_routes
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import version as version_service
from backend.tests.test_a_zadanie_is_never_silently_overwritten import _seed


def _file(tmp_path, slug):
    return tmp_path / slug / "docs" / "specs" / "versions" / "v0.2.0" / "customer-requirements.md"


def test_an_edit_of_the_text_the_editor_loaded_is_saved(db_session, tmp_path, monkeypatch) -> None:
    version = _seed(db_session, tmp_path, monkeypatch, slug="uprava")
    version_service.write_zadanie(db_session, version.id, "Zadanie v1")

    version_service.write_zadanie(db_session, version.id, "Zadanie v1\n\nDoplnená veta.", based_on="Zadanie v1\n")

    assert _file(tmp_path, "uprava").read_text(encoding="utf-8") == "Zadanie v1\n\nDoplnená veta."


def test_a_disk_that_changed_meanwhile_is_not_written_over(db_session, tmp_path, monkeypatch) -> None:
    version = _seed(db_session, tmp_path, monkeypatch, slug="zmenene")
    version_service.write_zadanie(db_session, version.id, "Zadanie v1")
    _file(tmp_path, "zmenene").write_text("Zadanie v2 — zapísal agent.", encoding="utf-8")

    with pytest.raises(version_service.ZadanieWouldBeOverwritten) as refused:
        version_service.write_zadanie(db_session, version.id, "Moja úprava v1.", based_on="Zadanie v1")

    assert refused.value.existing == "Zadanie v2 — zapísal agent."
    assert _file(tmp_path, "zmenene").read_text(encoding="utf-8") == "Zadanie v2 — zapísal agent."


def test_an_editor_that_saw_an_empty_disk_does_not_write_over_what_appeared(db_session, tmp_path, monkeypatch) -> None:
    version = _seed(db_session, tmp_path, monkeypatch, slug="prazdne")
    _file(tmp_path, "prazdne").parent.mkdir(parents=True)
    _file(tmp_path, "prazdne").write_text("Zadanie, ktoré tu medzitým pribudlo.", encoding="utf-8")

    with pytest.raises(version_service.ZadanieWouldBeOverwritten):
        version_service.write_zadanie(db_session, version.id, "Moje zadanie.", based_on="")


def test_the_route_passes_what_the_editor_started_from(db_session, tmp_path, monkeypatch) -> None:
    version = _seed(db_session, tmp_path, monkeypatch, slug="trasa")
    owner = db_session.get(User, db_session.get(Project, version.project_id).created_by)
    version_service.write_zadanie(db_session, version.id, "Zadanie v1")

    saved = versions_routes.write_zadanie(
        version.id,
        versions_routes._ZadanieWrite(content="Zadanie v1 a ešte veta.", based_on="Zadanie v1"),
        db_session,
        owner,
    )
    assert saved.status == "saved"

    with pytest.raises(HTTPException) as refused:
        versions_routes.write_zadanie(
            version.id, versions_routes._ZadanieWrite(content="Slepé uloženie."), db_session, owner
        )
    assert refused.value.status_code == 409
    assert refused.value.detail["existing"] == "Zadanie v1 a ešte veta."
