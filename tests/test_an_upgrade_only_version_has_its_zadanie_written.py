"""DEV-440 — a version that only upgrades the frame's libraries gets its Zadanie written by the cockpit.

20.11.2026, Orthodox Hermenia: the Manažér accepted „Povýšiť na v0.17.0“ on the „Nová verzia“ page; the task of the
version was plain — only the upgrade — yet the cockpit waited for a free-text Zadanie. Director: „keď robím len
povýšenie a nič iné a schválim to tak aby som ešte nemusel niečo dopisovať do zadania, veď úloha je jasná“. The cockpit
reads what rose since the previous finished version and the change notes in between (the same reading the Príprava
gets, DEV-354) and writes the Zadanie from them; without an upgrade it writes nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import delivery_statement, library_upgrades, nexshared
from tests.test_the_priprava_hears_what_an_upgrade_brought import _notes, _repo


def _brief(repo: Path) -> str | None:
    return library_upgrades.founding_brief(library_upgrades.upgrades(repo, [("tag", "1.0.0")], changelog=_notes))


def test_the_zadanie_names_what_rose_says_nothing_else_belongs_and_carries_the_changes_between(tmp_path):
    text = _brief(_repo(tmp_path / "p"))

    assert text is not None
    assert text.startswith("# Povýšenie knižníc rámu aplikácie\n\nVerzia prináša len povýšenie, ktoré Manažér prijal")
    assert "- šablóna aplikácie nex-app-kit: z v0.9.0 na v0.11.2\n- nex-shared: z v0.29.0 na v0.35.0" in text
    assert "Žiadna iná zmena aplikácie do tejto verzie nepatrí." in text
    assert "migrácia kit_0002 ruší jedinečnosť e-mailu" in text and "nové tlačidlá" in text
    assert "budúca verzia" not in text and "už bolo" not in text, "only the changes between the two releases"
    assert "## Hotové, keď" in text


def test_a_version_without_an_upgrade_gets_no_written_zadanie(tmp_path):
    repo = _repo(tmp_path / "p", before=("0.11.2", "0.35.0"), now=("0.11.2", "0.35.0"))

    assert _brief(repo) is None


def _project(db, source_path: Path) -> Project:
    s = uuid.uuid4().hex[:8]
    user = User(username=f"up_{s}", email=f"up_{s}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"U{s}",
        slug=f"u-{s}",
        type="standard",
        auth_mode="password",
        description="upgrade only",
        created_by=user.id,
        source_path=str(source_path),
        template="nsv-standard",
    )
    db.add(project)
    db.flush()
    _as(user)
    return project


def _as(user: User) -> None:
    from backend.core.security import get_current_user
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: user


def test_the_new_version_page_gets_the_written_zadanie_for_the_number_it_founds(
    client, db_session, tmp_path, monkeypatch
):
    project = _project(db_session, _repo(tmp_path / "p"))
    asked: list[str] = []

    def plan(_db, version):
        asked.append(version.version_number)
        return [("tag", "1.0.0")]

    monkeypatch.setattr(delivery_statement, "base_plan", plan)
    monkeypatch.setattr(nexshared, "fetch_changelog", _notes)

    body = client.get(f"/api/v1/projects/{project.id}/upgrade-brief", params={"version_number": "1.1.0"}).json()

    assert asked == ["1.1.0"], "the base is the finished version before the one being founded"
    assert body["text"].startswith("# Povýšenie knižníc rámu aplikácie")
    assert "z v0.9.0 na v0.11.2" in body["text"]


def test_a_project_without_a_checkout_gets_nothing(client, db_session, tmp_path):
    project = _project(db_session, tmp_path / "none")
    project.source_path = None
    db_session.flush()

    body = client.get(f"/api/v1/projects/{project.id}/upgrade-brief", params={"version_number": "1.1.0"}).json()

    assert body == {"text": None}
