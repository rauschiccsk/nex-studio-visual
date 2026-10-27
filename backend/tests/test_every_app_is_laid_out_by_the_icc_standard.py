"""DEV-242, DEV-243, DEV-244 — every app is laid out by one ICC standard, told to the agent in every Vizuál turn.

27.10.2026, NEX Knowledge 0.1.0 at 1920 px: numbers, dates and states in tables broke into two lines, a menu entry took
two lines, the sidebar width could not be changed, and every page capped itself leaving about 270 px empty on each
side. Director: „Tieto veci tiež chcem mať natrvalo zabudované do kokpitu, aby nebolo potrebné pri každom projekte
osobitne riešiť.“ nex-shared v0.25.0 carries the standard in its kit (Page, DataTable wrap, NavItem count, Sidebar
rememberWidthFor); the Vizuál brief — composed in every turn, so a running session sees it too — names the kit for a
project on v0.25.0 or newer, and the same rules in plain classes for one on an older kit, which it is not to raise on
its own (raising the kit is the Manažér's choice when he founds a version).
"""

from __future__ import annotations

import json

import pytest

from backend.services import claude_agent, orchestrator
from tests.test_orchestrator_v2_vizual import _make_version


def _project_on(tmp_path, monkeypatch, slug, version):
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(orchestrator, "_vizual_preview_rule", lambda _root: "")
    frontend = tmp_path / slug / "frontend"
    frontend.mkdir(parents=True)
    if version is not None:
        lock = {"packages": {"node_modules/nex-shared": {"version": version}}}
        (frontend / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")


def _brief(db_session, tmp_path, monkeypatch, version, request=None):
    v, project = _make_version(db_session)
    _project_on(tmp_path, monkeypatch, project.slug, version)
    return orchestrator._vizual_directive(db_session, v.id, request)


KIT = ["`<Page", "`wrap: true`", "`rememberWidthFor`", "`count`"]


@pytest.mark.parametrize("version", ["0.25.0", "0.25.3", "0.30.0", "1.0.0"])
def test_a_project_on_the_kit_with_the_standard_is_told_to_use_it(db_session, tmp_path, monkeypatch, version):
    brief = _brief(db_session, tmp_path, monkeypatch, version)

    assert orchestrator.layout_rule_for(version) in brief
    for name in KIT:
        assert name in orchestrator.layout_rule_for(version), name
    assert "whitespace-nowrap" not in orchestrator.layout_rule_for(version)


@pytest.mark.parametrize("version", ["0.24.0", "0.19.0", "0.9.12"])
def test_a_project_on_an_older_kit_gets_the_same_rules_in_plain_classes(db_session, tmp_path, monkeypatch, version):
    brief = _brief(db_session, tmp_path, monkeypatch, version)
    rule = orchestrator.layout_rule_for(version)

    assert rule in brief
    assert "whitespace-nowrap" in rule and f"v{version}" in rule
    assert "nepovyšuj z vlastnej iniciatívy" in rule
    assert "`<Page" not in rule


def test_a_project_whose_kit_cannot_be_read_gets_the_plain_rules(db_session, tmp_path, monkeypatch):
    brief = _brief(db_session, tmp_path, monkeypatch, None)

    assert orchestrator.layout_rule_for(None) in brief
    assert "whitespace-nowrap" in orchestrator.layout_rule_for(None)


def test_both_rules_name_the_whole_standard():
    for rule in (orchestrator.layout_rule_for("0.25.0"), orchestrator.layout_rule_for("0.19.0")):
        assert "celú šírku obrazovky" in rule
        assert "max-w-*" in rule and "mx-auto" in rule
        assert "jednom riadku" in rule
        assert "položky menu" in rule


def test_a_change_request_carries_it_too(db_session, tmp_path, monkeypatch):
    brief = _brief(db_session, tmp_path, monkeypatch, "0.25.0", request="Väčšie tlačidlo")

    assert orchestrator.layout_rule_for("0.25.0") in brief


def test_the_lowest_kit_of_a_web_project_decides(db_session, tmp_path, monkeypatch):
    """A Web project has two frontends — the rule must hold in both, so the older kit decides."""
    assert orchestrator.layout_rule_version(["0.25.0", "0.19.0"]) == "0.19.0"
    assert orchestrator.layout_rule_version(["0.25.0", None]) is None
    assert orchestrator.layout_rule_version(["0.26.0", "0.25.0"]) == "0.25.0"


def test_the_agent_charter_carries_it_for_screens_built_after_the_vizual():
    from pathlib import Path

    charter = (Path(orchestrator.__file__).resolve().parents[2] / "templates" / "ai-agent-charter.md").read_text(
        "utf-8"
    )
    assert "**Rozloženie obrazovky — štandard ICC (DEV-242, DEV-243, DEV-244).**" in charter
