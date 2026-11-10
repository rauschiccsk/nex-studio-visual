"""DEV-318 — what the Projektant reads in a new idea: the projects of the cockpit, their documentation (not code), what
the cockpit can found and a project's Zásobník. Its rules name every tool it has.

DEV-345 — every project of the cockpit, whoever it belongs to. Until 10.11.2026 it read only the projects of the person
asking (by the rule of the screens); Director: „Pri návrhu často potrebujem odkázať sa na riešenie, ktoré sme robili
v inom projekte, a kvôli tomu, aby projektant to pozrel nechcem zbytočne presúvať projekty medzi manažérmi.“ — and for
everyone who works in Nápady: „Áno“. Only reading, as before; the screens and the project's Poradca keep the owner rule.
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


def test_every_project_of_the_cockpit_is_listed_whoever_it_belongs_to(world, db_session):
    nobody = _user(db_session)  # owns no project at all
    for person in (world["me"], nobody):
        listed = _call(person, "projekty")
        assert world["mine"].slug in listed and world["theirs"].slug in listed and "opis Moja matrika" in listed
    assert listed.startswith("Projekty kokpitu (")


def test_each_project_names_its_manager(world):
    listed = _call(world["me"], "projekty")
    line = next(line for line in listed.splitlines() if world["theirs"].slug in line)
    other = world["other"]
    assert f"manažér: {' '.join(p for p in (other.first_name, other.last_name) if p) or other.username}" in line


def test_documentation_is_read_never_code(world):
    listing = _call(world["me"], "dokumentacia_projektu", projekt=world["mine"].slug)
    assert listing.splitlines()[1:] == ["- README.md", "- docs/specs/spec.md"]
    assert _call(world["me"], "dokumentacia_projektu", projekt=world["mine"].slug, cesta="docs/specs/spec.md") == (
        "# Špecifikácia\nKrsty a sobáše."
    )
    for wanted in ("backend/app.py", "docs/../backend/app.py", "../" + world["theirs"].slug + "/README.md"):
        with pytest.raises(ToolError, match="len dokumentáciu"):
            _call(world["me"], "dokumentacia_projektu", projekt=world["mine"].slug, cesta=wanted)


def test_another_persons_project_is_read_too(world):
    (world["root"].parent / world["theirs"].slug / "docs").mkdir(parents=True)
    (world["root"].parent / world["theirs"].slug / "docs" / "riesenie.md").write_text("# Riešenie", encoding="utf-8")

    assert _call(world["me"], "dokumentacia_projektu", projekt=world["theirs"].slug).splitlines()[1:] == [
        "- docs/riesenie.md"
    ]
    assert _call(world["me"], "dokumentacia_projektu", projekt=world["theirs"].slug, cesta="docs/riesenie.md") == (
        "# Riešenie"
    )
    assert "Zásobník projektu" in _call(world["me"], "zasobnik_projektu", projekt=world["theirs"].slug)


def test_a_project_that_is_not_in_the_cockpit_is_said(world):
    with pytest.raises(ToolError, match="v kokpite nie je"):
        _call(world["me"], "dokumentacia_projektu", projekt="nikde")


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
