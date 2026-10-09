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


# ── DEV-28: Poradca sees the Decision Cards the Manažér sees ─────────────────────────────────────────────────

_CARDS = {
    "id": "navrh-2",
    "source": "auditor_upfront",
    "round": 2,
    "round_max": 5,
    "decisions": [
        {
            "key": "velke-faktury",
            "question": "Čo s veľkou faktúrou, ktorá sa nezmestí do limitu?",
            "options": [
                {"id": "kusky", "label": "Posielať po menších potvrdených kúskoch", "recommended": True},
                {"id": "limit", "label": "Dlhší limit pre veľké súbory"},
            ],
        },
        {
            "key": "upozornenie-prehlad",
            "question": "Kedy zhasne upozornenie na Prehľade po výpadku?",
            "explanation": "Dnes by svietilo ešte 24 hodín.",
            "origin": "dosledok",
            "origin_of": "R2",
            "technical_detail": "compute_active_alerts: NIB-071 len pri outage_since; BEHAVIOR §4.3 + skúška.",
            "rationale": "Presne napĺňa R2.",
            "related": [{"key": "drobnosti", "why": "Oprava textov musí počítať s novým upozornením."}],
            "options": [
                {
                    "id": "hned",
                    "label": "Zhasnúť hneď, keď sa doručovanie obnoví",
                    "detail": "Kliknutie otvorí zoznam faktúr čakajúcich na doručenie.",
                    "recommended": True,
                },
                {"id": "24h", "label": "Nechať 24 hodín"},
            ],
        },
        {
            "key": "drobnosti",
            "question": "Čo s drobnými nepresnosťami Návrhu?",
            "options": [{"id": "opravit", "label": "Opraviť všetky naraz", "recommended": True}],
        },
    ],
}


def _consultation_world(world, *, decided: bool) -> None:
    db, version = world["db"], world["version"]
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="navrh",
            current_actor="ai_agent",
            status="blocked",
            block_reason="decision_needed",
            next_action="Manažér: rozhodni 2/3 (konzultácia).",
        )
    )
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="navrh",
            author="ai_agent",
            recipient="manazer",
            kind="consultation",
            content="Previerka našla tri body.",
            payload={"consultation": _CARDS},
        )
    )
    db.flush()
    if decided:
        db.add(
            PipelineMessage(
                version_id=version.id,
                stage="navrh",
                author="manazer",
                recipient="ai_agent",
                kind="answer",
                content="Čo s veľkou faktúrou, ktorá sa nezmestí do limitu? → Posielať po menších potvrdených kúskoch",
                payload={
                    "consultation_decision": {
                        "key": "velke-faktury",
                        "option_id": "kusky",
                        "label": "Posielať po menších potvrdených kúskoch",
                        "note": "Kúsky po 1 MB.",
                    }
                },
            )
        )
        db.flush()


async def test_stavba_shows_the_cards_word_for_word_and_which_one_is_next(world):
    _consultation_world(world, decided=True)
    out = await world["tools"].stavba({})
    assert "Karty rozhodnutí (konzultácia, kolo 2 z 5) — na rade je karta 2 z 3:" in out
    # Every option word for word — Poradca names it exactly, the way the card shows it.
    for decision in _CARDS["decisions"]:
        assert decision["question"] in out
        for option in decision["options"]:
            assert f"„{option['label']}“" in out
    assert "„Zhasnúť hneď, keď sa doručovanie obnoví“ (odporúčané)" in out
    assert "Dnes by svietilo ešte 24 hodín." in out
    lines = out.splitlines()
    first = next(i for i, line in enumerate(lines) if "Čo s veľkou faktúrou" in line)
    second = next(i for i, line in enumerate(lines) if "Kedy zhasne upozornenie" in line)
    assert "rozhodnutá" in lines[first] and "na rade" not in lines[first]
    assert "Manažér zvolil „Posielať po menších potvrdených kúskoch“" in "\n".join(lines[first:second])
    assert "Kúsky po 1 MB." in "\n".join(lines[first:second])
    assert "NA RADE" in lines[second]


async def test_stavba_without_a_consultation_shows_no_cards(world):
    _consultation_world(world, decided=False)
    db, version = world["db"], world["version"]
    state = db.query(PipelineState).filter_by(version_id=version.id).one()
    state.status, state.block_reason = "awaiting_manazer", None
    db.flush()
    out = await world["tools"].stavba({})
    assert "Karty rozhodnutí" not in out


def test_the_charter_points_poradca_at_the_cards_the_tool_returns():
    """The charter tells Poradca what to look for — the heading the tool really writes, word for word."""
    charter = (Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md").read_text(encoding="utf-8")
    assert "(nástroj `stavba` vráti „Karty rozhodnutí“)" in charter
    assert "doslova" in charter


async def test_stavba_shows_the_agents_own_plan_on_every_card(world):
    """DEV-30: the card's option descriptions, technical detail and rationale — what the Manažér sees, and the
    plan the agent already wrote. Without them Poradca rewrote the whole plan in every instruction."""
    _consultation_world(world, decided=True)
    out = await world["tools"].stavba({})
    card = out[out.index("Karta 2 [NA RADE]") : out.index("Karta 3 [")]
    assert "Technický detail: compute_active_alerts: NIB-071 len pri outage_since; BEHAVIOR §4.3 + skúška." in card
    assert "Zdôvodnenie odporúčania: Presne napĺňa R2." in card
    assert (
        "možnosť „Zhasnúť hneď, keď sa doručovanie obnoví“ (odporúčané) — "
        "Kliknutie otvorí zoznam faktúr čakajúcich na doručenie." in card
    )


def test_the_charter_asks_for_an_instruction_only_when_the_card_misses_something():
    charter = (Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md").read_text(encoding="utf-8")
    assert "Pokyn pre agenta napíš **len vtedy**" in charter
    assert "„Pokyn netreba — agent má v karte presný plán.“" in charter


def test_the_charter_asks_poradca_to_read_the_cards_again_before_advising():
    """DEV-30: in the morning conversation Poradca advised on card 10 from memory, without calling `stavba`."""
    charter = (Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md").read_text(encoding="utf-8")
    assert "Pred každou radou ku karte znova zavolaj `stavba`" in charter


async def test_stavba_names_the_cards_a_choice_hangs_together_with(world):
    """DEV-34: the link the agent wrote between two cards — Poradca sees it as the Manažér does."""
    _consultation_world(world, decided=True)
    out = await world["tools"].stavba({})
    card = out[out.index("Karta 2 [NA RADE]") : out.index("Karta 3 [")]
    assert (
        "Súvisí s kartou 3 („Čo s drobnými nepresnosťami Návrhu?“): Oprava textov musí počítať s novým upozornením."
        in card
    )


# ── DEV-33: Poradca sees the project's Zásobník before proposing a requirement for it ─────────────────────────
# 08.10.2026, NEX Inbox 1.7.0: Poradca offered the credit-note requirement for the Zásobník again („ak ju tam
# ešte nemáš"), although the Director had saved it as REQ-1 — no tool of Poradca read the Zásobník, and the
# „Uložiť do Zásobníka" click would have created a second, identical requirement.


def _backlog(world):
    from backend.db.models.backlog import BacklogItem

    db, project, version = world["db"], world["project"], world["version"]
    db.add_all(
        [
            BacklogItem(
                project_id=project.id,
                number=1,
                title="Dobropisy do Genesisu",
                description="Dobropis od dodávateľa sa má doručiť ako záporná faktúra. " + "Podrobnosti. " * 40,
                status="open",
                priority="high",
            ),
            BacklogItem(
                project_id=project.id, number=2, title="Tlač zoznamu faktúr", status="included", version_id=version.id
            ),
            BacklogItem(project_id=project.id, number=3, title="Export do Excelu", status="rejected"),
        ]
    )
    other = Project(
        name=f"Iný {uuid.uuid4().hex[:6]}",
        slug=f"iny-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=project.created_by,
    )
    db.add(other)
    db.flush()
    db.add(BacklogItem(project_id=other.id, number=1, title="Cudzia požiadavka", status="open"))
    db.flush()


async def test_zasobnik_lists_the_project_backlog_as_the_screen_names_it(world):
    _backlog(world)

    out = await world["tools"].zasobnik({})

    lines = out.splitlines()
    assert lines[0] == "Zásobník projektu — 3 požiadavky (REQ-číslo, stav, názov, popis):"
    assert lines[1].startswith(
        "REQ-1 [Otvorené, priorita vysoká] Dobropisy do Genesisu — "
        "Dobropis od dodávateľa sa má doručiť ako záporná faktúra."
    )
    assert lines[1].endswith(" …")
    assert lines[2] == "REQ-2 [Vo verzii 1.2.0, priorita stredná] Tlač zoznamu faktúr"
    assert lines[3] == "REQ-3 [Zamietnuté, priorita stredná] Export do Excelu"
    assert "Cudzia požiadavka" not in out


async def test_an_empty_zasobnik_says_so(world):
    assert await world["tools"].zasobnik({}) == "Zásobník projektu je prázdny."


def test_poradca_has_the_zasobnik_tool():
    names = [t.name for t in tools.build_tools(project_id=uuid.uuid4(), version_id=None, user_id=uuid.uuid4())]
    assert "zasobnik" in names


def test_the_charter_has_poradca_check_the_zasobnik_before_proposing_a_requirement():
    charter = (Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md").read_text(encoding="utf-8")
    assert "| `zasobnik` |" in charter
    assert "Pred požiadavkou do Zásobníka zavolaj `zasobnik`" in charter
    assert "menuj ju (REQ-číslo) a novú nenavrhuj" in charter
