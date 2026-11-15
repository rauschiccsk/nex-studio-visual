"""DEV-354 — the Príprava of a version hears what an upgrade of the template or nex-shared at its founding brought.

10.11.2026, NEX Knowledge 1.1.0 (template 0.9.0 → 0.11.2, nex-shared 0.29.0 → 0.35.0): the first Špecifikácia said
„bez povýšenia“ and that the template's tables did not change, though its migration kit_0002 dropped the uniqueness of
the e-mail; the release notes and the UAT guide did not mention the upgrade. The brief carried one sentence on
libraries, and only in the Vizuál. Director, on the ticket: „Súhlasím“; on doing it now: „súhlasím“.

Read where it is written: the frame's pins at what the previous finished version delivered against the pins now, and
the change notes between the two releases. A version without an upgrade — and a first version — get nothing more.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from backend.db.models.versions import Version
from backend.services import claude_agent, library_upgrades, nexshared, orchestrator
from tests.test_orchestrator_v2_priprava_dial import _gate_report, _make_version, _seed_state, _stub_invoke

KIT_NOTES = """# Changelog

## v0.12.0
- budúca verzia, ešte nie

## v0.11.2
- [oprava] spoločné úložiská kopíruje pravidlo shared-copies

## v0.10.0
- [API] migrácia kit_0002 ruší jedinečnosť e-mailu

## v0.9.0
- už bolo
"""
SHARED_NOTES = "# Changelog\n\n## v0.35.0\n- [vzhľad] nové tlačidlá\n\n## v0.29.0\n- už bolo\n"


def _frame(kit: str, shared: str) -> str:
    return json.dumps(
        {
            "dependencies": {
                "nex-app-kit": nexshared.git_dependency("nex-app-kit", kit),
                "nex-shared": nexshared.git_dependency("nex-shared", shared),
            }
        },
        indent=2,
    )


def _repo(root: Path, *, before=("0.9.0", "0.29.0"), now=("0.11.2", "0.35.0"), tag="v1.0.0") -> Path:
    """A project whose previous version (tagged) was on ``before`` and whose frame is on ``now``."""
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    (root / "frontend").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "frontend" / "package.json").write_text(_frame(*before), encoding="utf-8")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "the previous version"], check=True)
    subprocess.run([*git, "tag", tag], check=True)
    (root / "frontend" / "package.json").write_text(_frame(*now), encoding="utf-8")
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-am", "chore(deps): bump nex-app-kit"], check=True)
    return root


def _notes(url: str) -> str:
    return KIT_NOTES if "nex-app-kit" in url else SHARED_NOTES


def test_the_brief_names_each_package_its_releases_and_the_changes_between(tmp_path):
    repo = _repo(tmp_path / "p")

    block = library_upgrades.brief_block(library_upgrades.upgrades(repo, [("tag", "1.0.0")], changelog=_notes))

    assert "- šablóna aplikácie nex-app-kit: z v0.9.0 na v0.11.2\n- nex-shared: z v0.29.0 na v0.35.0" in block
    assert "migrácia kit_0002 ruší jedinečnosť e-mailu" in block and "pravidlo shared-copies" in block
    assert "nové tlačidlá" in block
    assert "budúca verzia" not in block and "už bolo" not in block, "only the changes between the two releases"
    assert "Nepíš, že verzia je bez povýšenia." in block


def test_a_version_without_an_upgrade_hears_nothing_more(tmp_path):
    repo = _repo(tmp_path / "p", now=("0.9.0", "0.29.0"))

    assert library_upgrades.brief_for(repo, [("tag", "1.0.0")]) == ""


def test_a_first_version_hears_nothing_more(tmp_path):
    repo = _repo(tmp_path / "p")

    assert library_upgrades.brief_for(repo, []) == ""


def test_change_notes_that_cannot_be_read_are_said_so(tmp_path):
    repo = _repo(tmp_path / "p")

    block = library_upgrades.brief_block(library_upgrades.upgrades(repo, [("tag", "1.0.0")], changelog=lambda _u: ""))

    assert "z v0.9.0 na v0.11.2" in block
    assert "Zápisy zmien nex-app-kit sa nepodarilo načítať" in block


@pytest.fixture
def founded_on_an_upgrade(db_session, monkeypatch, tmp_path):
    """Version 1.1.0 founded on an upgrade, version 1.0.0 released before it — the case of NEX Knowledge."""
    version, project = _make_version(db_session)
    version.version_number = "1.1.0"
    db_session.add(Version(project_id=project.id, version_number="1.0.0", status="released"))
    db_session.flush()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    _repo(tmp_path / project.slug)
    monkeypatch.setattr(nexshared, "fetch_changelog", _notes)
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    return version


async def test_the_priprava_round_carries_it(db_session, founded_on_an_upgrade, monkeypatch):
    captured = _stub_invoke(monkeypatch, _gate_report("priprava"))

    await orchestrator.run_dispatch(db_session, founded_on_an_upgrade.id)

    assert library_upgrades.HEADING in captured["prompt"]
    assert "šablóna aplikácie nex-app-kit: z v0.9.0 na v0.11.2" in captured["prompt"]
    assert "migrácia kit_0002 ruší jedinečnosť e-mailu" in captured["prompt"]


async def test_a_reading_that_does_not_finish_tells_where_to_look(db_session, founded_on_an_upgrade, monkeypatch):
    from backend.config.settings import settings

    def _slow(_repo, _steps):
        import time

        time.sleep(1)
        return "never"

    monkeypatch.setattr(settings, "library_upgrades_cap_seconds", 0.05)
    monkeypatch.setattr(library_upgrades, "brief_for", _slow)
    captured = _stub_invoke(monkeypatch, _gate_report("priprava"))

    await orchestrator.run_dispatch(db_session, founded_on_an_upgrade.id)
    await asyncio.sleep(0)

    assert library_upgrades.UNKNOWN in captured["prompt"]
