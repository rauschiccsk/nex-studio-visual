"""DEV-359 — Projektant and Poradca see how old each document of a project is.

11.11.2026: Projektant wrote of NEX Inbox „AI cez Ollama na vlastnom serveri“ from docs/specs/summary.md — a summary of
13.06.2026 for version 1.0.1 nobody had updated; NEX Inbox was at 1.10.3 and runs on Claude. The list of documents gave
bare paths, so the June summary looked as valid as the documents of 1.10.3. Director, on the ticket: „Áno založ.“; on
doing it now: „Súhlasím“.

Every document carries the date of its last change in the project's git, a version's document its version, and what
has not changed since the last finished version is marked; the charters of both say what decides the project's state.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from backend.db.models.versions import Version
from backend.services import claude_agent, doc_ages
from backend.services.poradca import tools

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def _commit(root: Path, rel: str, text: str, when: str, tag: str | None = None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    env = {"GIT_AUTHOR_DATE": f"{when}T10:00:00", "GIT_COMMITTER_DATE": f"{when}T10:00:00", "PATH": "/usr/bin:/bin"}
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", rel], check=True, env=env)
    if tag:
        subprocess.run([*git, "tag", tag], check=True, env=env)


@pytest.fixture
def inbox(tmp_path) -> Path:
    """NEX Inbox as on 11.11.2026, in miniature: the June summary, and the documents of 1.10.3."""
    root = tmp_path / "nex-inbox"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _commit(root, "docs/specs/summary.md", "AI cez Ollama na vlastnom serveri\n", "2026-06-13", tag="v1.0.1")
    _commit(root, "docs/specs/versions/v1.10.3/specification.md", "AI cez Claude Code\n", "2026-11-12", tag="v1.10.3")
    (root / "docs" / "specs" / "draft.md").write_text("rozpísané, nie v gite\n", encoding="utf-8")
    return root


def _line(text: str, path: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"- {path} "))


def test_the_old_summary_shows_its_date_and_is_marked_older_than_the_last_version(inbox):
    text = doc_ages.listing(str(inbox), "nex-inbox", "1.10.3")

    assert "Posledná dokončená verzia: 1.10.3 (12.11.2026)." in text
    assert doc_ages.RULE in text
    assert _line(text, "docs/specs/summary.md") == "- docs/specs/summary.md — 13.06.2026 — starší než verzia 1.10.3"
    assert _line(text, "docs/specs/versions/v1.10.3/specification.md") == (
        "- docs/specs/versions/v1.10.3/specification.md — 12.11.2026, verzia 1.10.3"
    )
    assert _line(text, "docs/specs/draft.md") == "- docs/specs/draft.md — dátum neznámy (nie je v gite)"


def test_before_the_first_finished_version_nothing_is_marked_older(inbox):
    text = doc_ages.listing(str(inbox), "nex-inbox", None)

    assert "Posledná dokončená verzia" not in text and "starší než" not in text
    assert _line(text, "docs/specs/summary.md") == "- docs/specs/summary.md — 13.06.2026"


@pytest.fixture
def project_on_disk(db_session, monkeypatch, inbox):
    from tests.test_orchestrator_v2_priprava_dial import _make_version

    version, project = _make_version(db_session)
    version.version_number, version.status = "1.10.3", "released"
    db_session.flush()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", inbox.parent)
    monkeypatch.setattr(project, "slug", "nex-inbox")
    db_session.flush()
    from tests.test_poradca_tools_db import _Ctx

    monkeypatch.setattr(tools, "SessionLocal", lambda: _Ctx(db_session))
    return project


async def test_poradca_reads_the_same_list(project_on_disk):
    poradca = tools.PoradcaTools(project_id=project_on_disk.id, version_id=None, user_id=uuid.uuid4())

    text = await poradca.dokumenty({})

    assert _line(text, "docs/specs/summary.md") == "- docs/specs/summary.md — 13.06.2026 — starší než verzia 1.10.3"


async def test_projektant_reads_the_same_list(project_on_disk, monkeypatch, db_session):
    from backend.services.poradca import idea_tools
    from tests.test_poradca_tools_db import _Ctx

    monkeypatch.setattr(idea_tools, "SessionLocal", lambda: _Ctx(db_session))
    monkeypatch.setattr(idea_tools.IdeaTools, "_project", lambda self, db, ref: project_on_disk)

    text = await idea_tools.IdeaTools(user_id=uuid.uuid4()).dokumentacia_projektu({"projekt": "nex-inbox"})

    assert _line(text, "docs/specs/summary.md") == "- docs/specs/summary.md — 13.06.2026 — starší než verzia 1.10.3"


def test_both_charters_say_what_decides_the_projects_state():
    for charter in ("poradca-charter.md", "poradca-ideas-charter.md"):
        text = " ".join((TEMPLATES / charter).read_text(encoding="utf-8").split())
        assert doc_ages.RULE in text, charter


def test_poradca_is_given_the_tool_on_the_documents_screen():
    names = {t.name for t in tools.build_tools(project_id=uuid.uuid4(), version_id=None, user_id=uuid.uuid4())}

    assert "dokumenty" in names
    assert "dokumenty" in tools.SCREENS["Dokumenty"]


def test_a_released_version_counts_as_the_last_finished_one(db_session):
    from tests.test_orchestrator_v2_priprava_dial import _make_version

    version, project = _make_version(db_session)
    version.version_number, version.status = "1.2.0", "released"
    db_session.add(Version(project_id=project.id, version_number="1.10.0", status="released"))
    db_session.flush()

    assert tools.latest_finished_number(db_session, project.id) == "1.10.0"  # by semver, not as text
