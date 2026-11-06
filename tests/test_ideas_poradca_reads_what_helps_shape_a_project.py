"""DEV-318 — what Poradca reads in a new idea: the projects the person may open, their documentation (not code), what
the cockpit can found and a project's Zásobník — each for the person asking, by the rule of the screens (the admin
account every project, anyone else their own). Its rules name every tool it has.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services.poradca import idea_tools, runner, tools
from backend.services.poradca.mcp_server import ToolError


class _Ctx:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


def _user(db: Any, username: str | None = None, role: str = "shu") -> User:
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role=role,
    )
    db.add(u)
    db.flush()
    return u


def _project(db: Any, owner: User, name: str) -> Project:
    p = Project(
        name=name,
        slug=f"p-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description=f"opis {name}",
        created_by=owner.id,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture()
def world(db_session, tmp_path, monkeypatch):
    from backend.services import claude_agent

    monkeypatch.setattr(idea_tools, "SessionLocal", lambda: _Ctx(db_session))
    monkeypatch.setattr(tools, "SessionLocal", lambda: _Ctx(db_session))
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    me, other = _user(db_session), _user(db_session)
    mine, theirs = _project(db_session, me, "Moja matrika"), _project(db_session, other, "Cudzí projekt")
    root = tmp_path / mine.slug
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "docs" / "specs" / "spec.md").write_text("# Špecifikácia\nKrsty a sobáše.", encoding="utf-8")
    (root / "README.md").write_text("# Moja matrika", encoding="utf-8")
    (root / "docs" / ".env").write_text("TOKEN=x", encoding="utf-8")
    (root / "docs" / "schema.sql").write_text("CREATE TABLE x ();", encoding="utf-8")
    (root / "backend").mkdir()
    (root / "backend" / "app.py").write_text("SECRET = 1", encoding="utf-8")
    return {"db": db_session, "me": me, "other": other, "mine": mine, "theirs": theirs, "root": root}


def _tool(user: User, name: str):
    return next(t for t in idea_tools.build_idea_tools(user_id=user.id) if t.name == name)


def _call(user: User, name: str, **args) -> str:
    return asyncio.run(_tool(user, name).handler(args))


def test_the_projects_are_the_ones_the_person_may_open(world, db_session):
    mine = _call(world["me"], "projekty")
    assert world["mine"].slug in mine and "opis Moja matrika" in mine
    assert world["theirs"].slug not in mine
    admin = _user(db_session, username="admin", role="ri")
    everything = _call(admin, "projekty")
    assert world["mine"].slug in everything and world["theirs"].slug in everything


def test_documentation_is_read_never_code(world):
    listing = _call(world["me"], "dokumentacia_projektu", projekt=world["mine"].slug)
    assert listing.splitlines()[1:] == ["- README.md", "- docs/specs/spec.md"]
    assert _call(world["me"], "dokumentacia_projektu", projekt=world["mine"].slug, cesta="docs/specs/spec.md") == (
        "# Špecifikácia\nKrsty a sobáše."
    )
    for wanted in ("backend/app.py", "docs/../backend/app.py", "../" + world["theirs"].slug + "/README.md"):
        with pytest.raises(ToolError, match="len dokumentáciu"):
            _call(world["me"], "dokumentacia_projektu", projekt=world["mine"].slug, cesta=wanted)


def test_another_persons_project_is_not_read(world):
    for name, args in (("dokumentacia_projektu", {}), ("zasobnik_projektu", {})):
        with pytest.raises(ToolError, match="nie je v právach"):
            _call(world["me"], name, projekt=world["theirs"].slug, **args)


def test_the_founding_options_are_the_forms(world, db_session):
    from backend.api.routes.projects import get_create_presets

    presets = get_create_presets(db_session)
    said = _call(world["me"], "moznosti_zalozenia")
    assert presets.templates and all(f"{t.id} — {t.label}" in said for t in presets.templates)
    assert ("dostupný: áno" in said) == presets.web_available


def test_the_founding_options_say_when_web_is_not_available(world, db_session, monkeypatch):
    from backend.services import project_surfaces

    monkeypatch.setattr(project_surfaces, "WEB_PROJECTS_AVAILABLE", False)
    assert f"dostupný: nie — {project_surfaces.WEB_NOT_YET}" in _call(world["me"], "moznosti_zalozenia")


def test_the_zasobnik_of_a_project(world, db_session):
    db_session.add(BacklogItem(project_id=world["mine"].id, number=1, title="Skupiny podľa farností", description="d"))
    db_session.flush()
    assert "REQ-1" in _call(world["me"], "zasobnik_projektu", projekt=world["mine"].slug)


def test_the_rules_of_navrhy_name_every_tool_of_an_idea():
    charter = Path(runner._IDEA_CHARTER).read_text(encoding="utf-8")
    names = [t.name for t in idea_tools.build_idea_tools(user_id=uuid.uuid4())]
    assert sorted(names) == sorted(
        [
            "znalostna_baza",
            "znalostna_baza_dokument",
            "projekty",
            "dokumentacia_projektu",
            "moznosti_zalozenia",
            "zasobnik_projektu",
        ]
    )
    assert [n for n in names if f"`{n}`" not in charter] == []
