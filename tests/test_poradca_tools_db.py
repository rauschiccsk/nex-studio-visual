"""Nástroje Poradcu nad skutočnými dátami (ICCINT-167): stavba, plán úloh, git, kroky agenta, zber tajomstiev.

Databáza je izolovaná skúšobná; git je skutočný repozitár v dočasnom priečinku; docker sa nevolá.
Hodnoty tajomstiev sú umelé.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import build_sandbox, claude_agent, uat_provisioner
from backend.services.poradca import context, tools
from backend.services.poradca.mcp_server import ToolError


class _Ctx:
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def world(db_session, tmp_path, monkeypatch):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e.com", password_hash="x", role="ri"
    )
    db_session.add(user)
    db_session.flush()
    slug = f"demo-{uuid.uuid4().hex[:6]}"
    project = Project(
        name=f"Demo {uuid.uuid4().hex[:8]}",
        slug=slug,
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number="1.2.0")
    db_session.add(version)
    db_session.flush()
    monkeypatch.setattr(tools, "SessionLocal", lambda: _Ctx(db_session))
    root = tmp_path / "projects"
    (root / slug).mkdir(parents=True)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", root)
    t = tools.PoradcaTools(project_id=project.id, version_id=version.id, user_id=user.id)
    return {"db": db_session, "project": project, "version": version, "tools": t, "dir": root / slug, "tmp": tmp_path}


async def test_stavba_reads_state_buttons_and_messages_like_the_screen(world):
    db, version = world["db"], world["version"]
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="paused",
            pause_reason="manazer",
            next_action="Pokračuj cez Pokračovať.",
        )
    )
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="programovanie",
            author="ai_agent",
            recipient="manazer",
            kind="gate_report",
            content="Hotová úloha 1.1.1.",
        )
    )
    db.flush()
    out = await world["tools"].stavba({})
    assert "Verzia 1.2.0" in out and "Fáza: programovanie; stav: paused" in out
    assert "„Pokračovať“ (pokracovat)" in out
    assert "Hotová úloha 1.1.1." in out


async def test_stavba_before_the_build_and_for_an_unknown_version(world):
    assert "ešte nezačala" in await world["tools"].stavba({})
    with pytest.raises(ToolError, match="neexistuje"):
        await world["tools"].stavba({"verzia": "9.9.9"})


async def test_plan_uloh_lists_the_tree_with_states(world):
    db, project, version = world["db"], world["project"], world["version"]
    assert "nemá plán úloh" in await world["tools"].plan_uloh({})
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Základ", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schéma", status="todo")
    db.add(feat)
    db.flush()
    db.add(Task(feat_id=feat.id, number=1, title="Tabuľka faktúr", task_type="backend", status="done"))
    db.flush()
    out = await world["tools"].plan_uloh({})
    assert "EPIC 1. Základ [planned]" in out
    assert "úloha 1.1.1 Tabuľka faktúr [done]" in out


def _git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e"}
    import os

    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, **env}
    ).stdout


async def test_git_history_and_one_change_from_a_real_repository(world):
    d = world["dir"]
    _git(d, "init", "-q")
    (d / "app.py").write_text("print('ahoj')\n")
    _git(d, "add", "app.py")
    _git(d, "commit", "-q", "-m", "Prvá zmena")
    (d / "README.md").write_text("x\n")
    _git(d, "add", "README.md")
    _git(d, "commit", "-q", "-m", "Druhá zmena")
    sha = _git(d, "rev-parse", "--short", "HEAD").strip()

    history = await world["tools"].git_historia({"pocet": 5})
    assert "Druhá zmena" in history and "Prvá zmena" in history
    only_app = await world["tools"].git_historia({"cesta": "app.py"})
    assert "Prvá zmena" in only_app and "Druhá zmena" not in only_app
    change = await world["tools"].git_zmena({"commit": sha})
    assert "Druhá zmena" in change and "README.md" in change
    with pytest.raises(ToolError, match="nenašiel"):
        await world["tools"].git_zmena({"commit": "deadbeef"})


async def test_zaznam_agenta_reads_the_build_agents_transcripts(world, monkeypatch):
    home = world["tmp"] / "claude"
    monkeypatch.setattr(build_sandbox, "_CLAUDE_HOME_DIR", str(home))
    out = await world["tools"].zaznam_agenta({})
    assert "ešte nepracoval" in out
    transcripts = home / "projects" / build_sandbox.session_dir_name(str(world["dir"]))
    transcripts.mkdir(parents=True)
    (transcripts / "s.jsonl").write_text(
        '{"timestamp":"2026-10-05T10:00:00Z","message":{"content":[{"type":"tool_use","id":"1",'
        '"name":"Edit","input":{"file_path":"' + str(world["dir"]) + '/app.py"}}]}}\n'
    )
    assert "Edit: app.py" in await world["tools"].zaznam_agenta({"pocet": 10})


def test_uat_installations_follow_the_customers_table_and_never_prod(world, tmp_path, monkeypatch):
    db, project = world["db"], world["project"]
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path / "uat")
    db.add(Customer(project_id=project.id, name="Andros", slug="ANDROS"))
    db.add(Customer(project_id=project.id, name="Bez inštalácie", slug="nikto"))
    db.flush()
    inst_dir = tmp_path / "uat" / "andros" / project.slug
    inst_dir.mkdir(parents=True)
    (inst_dir / "docker-compose.yml").write_text("services: {}\n")
    found = context.uat_installations(db, project)
    assert [(i.customer_slug, i.directory) for i in found] == [("andros", inst_dir)]


def test_known_secret_values_come_from_env_uat_and_the_vault(world, tmp_path, monkeypatch):
    db, project = world["db"], world["project"]
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-env-oauth-0001")
    monkeypatch.setenv("DATABASE_URL", "postgresql+pg8000://app:fake-db-pass-0002@db:5432/app")
    db.add(Customer(project_id=project.id, name="Andros", slug="andros"))
    db.flush()
    inst_dir = tmp_path / "uat" / "andros" / project.slug
    inst_dir.mkdir(parents=True)
    (inst_dir / "docker-compose.yml").write_text("services: {}\n")
    (inst_dir / ".env").write_text("DB_PASSWORD=fake-uat-pass-0003\nAPP_NAME=demo-aplikacia\n")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.env").write_text("API_TOKEN='fake-vault-token-0004'\nHOST=server.example\n")
    (vault / "b.json").write_text('{"smtp": {"password": "fake-vault-json-0005", "user": "jano"}}')
    from backend.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings, "credentials_storage_path", str(vault))
    from backend.db.models.credentials import Credential

    for name in ("a.env", "b.json"):
        db.add(Credential(title=name, file_path=str(vault / name)))
    db.add(Credential(title="chýba", file_path=str(vault / "zmazany.env")))  # nečitateľný záznam sa preskočí
    db.flush()
    values = set(context.known_secret_values(db, project))
    for fake in (
        "fake-env-oauth-0001",
        "fake-db-pass-0002",
        "fake-uat-pass-0003",
        "fake-vault-token-0004",
        "fake-vault-json-0005",
    ):
        assert fake in values, fake
    # Nie-tajné hodnoty sa neskrývajú — inak by odpoveď prestala dávať zmysel.
    for plain in ("demo-aplikacia", "server.example", "jano"):
        assert plain not in values, plain
